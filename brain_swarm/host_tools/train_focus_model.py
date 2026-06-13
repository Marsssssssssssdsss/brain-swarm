#!/usr/bin/env python3
"""
Train a TFLite Micro INT8 quantized model for real-time EEG brain state detection.

Target: ESP32-S3 with TensorFlow Lite Micro.

Pipeline:
  1. Generate synthetic EEG data with scipy.signal (8 brain states)
  2. Extract band-power features matching firmware DSP (4ch × 5 bands = 20 features)
  3. Build a ~500-param dense network
  4. Train with early stopping
  5. Convert to INT8 TFLite via post-training quantization
  6. Generate C header for ESP32-S3 firmware
  7. Evaluate and verify inference

Dependencies: tensorflow, numpy, scipy, sklearn
Run: python train_focus_model.py
"""

import os
import sys
import argparse

import numpy as np
from scipy import signal as sp_signal
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, classification_report
import tensorflow as tf

# ---------------------------------------------------------------------------
# Constants — match firmware configuration
# ---------------------------------------------------------------------------
FS = 250.0                # Sampling rate (Hz)
N_FFT = 512               # FFT size
N_CHANNELS = 4            # Fp1, Fp2, C3, C4
N_CLASSES = 8
N_BANDS = 5               # delta, theta, alpha, beta, gamma
N_FEATURES = N_CHANNELS * N_BANDS  # 20

CLASS_NAMES = [
    "DROWSY", "RELAXED", "CALM", "ATTENTIVE",
    "FOCUSED", "DEEP_FOCUS", "HYPERFOCUS", "NOISE",
]

CHANNEL_NAMES = ["Fp1", "Fp2", "C3", "C4"]

# Frequency band definitions (Hz)
BANDS = {
    "delta": (0.5, 4.0),
    "theta": (4.0, 8.0),
    "alpha": (8.0, 13.0),
    "beta":  (13.0, 30.0),
    "gamma": (30.0, 45.0),
}
BAND_NAMES = list(BANDS.keys())

# Relative band-power profiles per class [delta, theta, alpha, beta, gamma]
# These define the expected spectral signature of each brain state.
CLASS_PROFILES = {
    "DROWSY":      [0.60, 0.30, 0.05, 0.03, 0.02],
    "RELAXED":     [0.10, 0.05, 0.70, 0.10, 0.05],
    "CALM":        [0.15, 0.15, 0.35, 0.25, 0.10],
    "ATTENTIVE":   [0.05, 0.05, 0.15, 0.55, 0.20],
    "FOCUSED":     [0.02, 0.03, 0.05, 0.60, 0.30],
    "DEEP_FOCUS":  [0.02, 0.02, 0.03, 0.45, 0.48],
    "HYPERFOCUS":  [0.01, 0.01, 0.02, 0.22, 0.74],
    "NOISE":       None,  # random Dirichlet per sample
}

SAMPLES_PER_CLASS = 2000
SEED = 42

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_TFLITE = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "firmware", "esp32s3", "main", "focus_detector_model.tflite")
)
OUTPUT_HEADER = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "firmware", "esp32s3", "main", "focus_detector_model.h")
)

np.random.seed(SEED)
tf.random.set_seed(SEED)


# ---------------------------------------------------------------------------
# 1. Synthetic EEG generation
# ---------------------------------------------------------------------------
def design_band_filters():
    """Design 4th-order Butterworth SOS bandpass filters for each EEG band."""
    sos_filters = {}
    for name, (lo, hi) in BANDS.items():
        sos = sp_signal.butter(4, [lo, hi], btype="band", analog=False, fs=FS, output="sos")
        sos_filters[name] = sos
    return sos_filters


def generate_eeg_sample(sos_filters, profile_weights):
    """Generate one multi-channel synthetic EEG sample.

    For each channel: white noise is filtered into the 5 EEG bands, scaled by
    the profile weights, and summed.  Extra samples are generated and discarded
    to avoid filter transient artifacts.

    Returns: (N_CHANNELS, N_FFT) float32 array.
    """
    transient_pad = 256  # extra samples for filter settling
    total_len = N_FFT + transient_pad

    channels = []
    for ch in range(N_CHANNELS):
        noise = np.random.randn(total_len)
        signal = np.zeros(total_len)

        for bi, name in enumerate(BAND_NAMES):
            sos = sos_filters[name]
            band_signal = sp_signal.sosfilt(sos, noise)
            per_band_gain = 0.8 + 0.4 * np.random.rand()
            signal += profile_weights[bi] * per_band_gain * band_signal

        signal += 0.05 * np.random.randn(total_len)
        signal = signal / np.std(signal) * 15.0    # scale to ~15 uV RMS
        channels.append(signal[-N_FFT:])            # discard transient

    return np.array(channels, dtype=np.float32)


def generate_dataset(sos_filters, samples_per_class=SAMPLES_PER_CLASS):
    """Generate the complete synthetic EEG dataset.

    Args:
        sos_filters: dict of SOS filter coefficients per band
        samples_per_class: number of samples to generate per class

    Returns:
        X: (N_total, N_CHANNELS, N_FFT) raw EEG
        y: (N_total, N_CLASSES) one-hot labels
    """
    X_list, y_list = [], []
    class_keys = list(CLASS_PROFILES.keys())

    for ci, class_name in enumerate(class_keys):
        profile = CLASS_PROFILES[class_name]
        for i in range(samples_per_class):
            if class_name == "NOISE":
                weights = np.random.dirichlet(np.ones(N_BANDS) * 0.5)
            else:
                weights = np.array(profile, dtype=np.float64)
                jitter = 0.05 * np.random.randn(N_BANDS)
                weights = np.clip(weights + jitter, 0.01, 1.0)
                weights /= weights.sum()

            sample = generate_eeg_sample(sos_filters, weights)
            X_list.append(sample)
            y_list.append(ci)

    X = np.array(X_list, dtype=np.float32)
    y = tf.keras.utils.to_categorical(y_list, num_classes=N_CLASSES)
    return X, y


# ---------------------------------------------------------------------------
# 2. Feature extraction (matches firmware DSP pipeline)
# ---------------------------------------------------------------------------
def extract_features(X_raw):
    """Extract 20 band-power features from raw EEG.

    Firmware-matching pipeline:
      1. Apply Hanning window (512-point)
      2. Compute real FFT @ 250 Hz
      3. Sum squared magnitude in each of 5 bands per channel
      4. Per-channel z-score normalisation

    Args:
        X_raw: (N, N_CHANNELS, N_FFT) raw time-domain EEG

    Returns:
        features: (N, 20) normalised band-power features
    """
    n_samples = X_raw.shape[0]

    # Precompute FFT bin indices for each band
    freqs = np.fft.rfftfreq(N_FFT, d=1.0 / FS)
    band_bins = {}
    for name, (lo, hi) in BANDS.items():
        band_bins[name] = np.where((freqs >= lo) & (freqs <= hi))[0]

    window = np.hanning(N_FFT).astype(np.float32)
    features = np.zeros((n_samples, N_FEATURES), dtype=np.float32)

    for si in range(n_samples):
        for ch in range(N_CHANNELS):
            windowed = X_raw[si, ch, :] * window
            fft_mag = np.abs(np.fft.rfft(windowed))
            fft_power = fft_mag ** 2
            for bi, name in enumerate(BAND_NAMES):
                idx = band_bins[name]
                features[si, ch * N_BANDS + bi] = np.sum(fft_power[idx])

    # Per-channel z-score normalisation
    for ch in range(N_CHANNELS):
        start = ch * N_BANDS
        end = start + N_BANDS
        mu = np.mean(features[:, start:end], axis=0)
        sd = np.std(features[:, start:end], axis=0)
        sd[sd < 1e-12] = 1.0
        features[:, start:end] = (features[:, start:end] - mu) / sd

    return features


# ---------------------------------------------------------------------------
# 3. Model definition  (~500 params ➜ fits in 4 KB)
# ---------------------------------------------------------------------------
def build_model():
    """Build a tiny dense network suitable for TFLite Micro on ESP32-S3."""
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(N_FEATURES,), name="eeg_features"),
        tf.keras.layers.Dense(16, activation="relu", name="hidden"),
        tf.keras.layers.Dense(N_CLASSES, activation="softmax", name="output"),
    ], name="focus_detector")
    return model


# ---------------------------------------------------------------------------
# 4. Training
# ---------------------------------------------------------------------------
def train_model(model, X_train, y_train, X_val, y_val, max_epochs=200):
    """Compile and train with early stopping + learning-rate reduction."""
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=20, restore_best_weights=True, verbose=1,
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5, patience=5, min_lr=1e-5, verbose=1,
        ),
    ]

    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=max_epochs,
        batch_size=32,
        callbacks=callbacks,
        verbose=2,
    )
    return model, history


# ---------------------------------------------------------------------------
# 5. TFLite INT8 conversion  (post-training quantisation)
# ---------------------------------------------------------------------------
def representative_dataset_gen(features_val, batch_size=32):
    """Yield batches of float32 features for INT8 calibration."""
    n = features_val.shape[0]
    for i in range(0, n, batch_size):
        yield [features_val[i : i + batch_size].astype(np.float32)]


def convert_to_tflite_int8(model, features_val, output_path):
    """Convert Keras model to INT8 quantised TFLite flatbuffer."""
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = lambda: representative_dataset_gen(features_val)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_model = converter.convert()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(tflite_model)

    print(f"\n  TFLite INT8 model → {output_path}")
    print(f"  Size: {len(tflite_model)} bytes  ({len(tflite_model)/1024:.1f} KB)")
    return tflite_model


# ---------------------------------------------------------------------------
# 6. C header generation
# ---------------------------------------------------------------------------
def generate_c_header(tflite_model, output_path):
    """Write a C header with the model as a const unsigned char array."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    hex_bytes = ", ".join(f"0x{b:02x}" for b in tflite_model)
    array_len = len(tflite_model)

    lines = [
        "#ifndef FOCUS_DETECTOR_MODEL_H_",
        "#define FOCUS_DETECTOR_MODEL_H_",
        "",
        "#include <cstdint>",
        "",
        "// TFLite Micro INT8 quantised model for EEG brain-state detection.",
        f"// Model size: {array_len} bytes  ({array_len/1024:.1f} KB)",
        "// Input:  20 features (int8, quantised per-channel band powers)",
        "// Output: 8 classes   (int8, quantised softmax scores)",
        "//",
        "// Class indices:",
    ]
    for i, name in enumerate(CLASS_NAMES):
        lines.append(f"//   {i}: {name}")
    lines.append("//")
    lines.append("// Channels (each contributes 5 band powers):")
    for ch in CHANNEL_NAMES:
        lines.append(f"//   {ch}: delta, theta, alpha, beta, gamma")
    lines.append("//")
    lines.append(f"alignas(16) const unsigned char focus_detector_model_tflite[] = {{")
    lines.append(f"  {hex_bytes}")
    lines.append("};")
    lines.append(f"const unsigned int focus_detector_model_tflite_len = {array_len};")
    lines.append("")
    lines.append("#endif  // FOCUS_DETECTOR_MODEL_H_")
    lines.append("")

    header = "\n".join(lines)
    with open(output_path, "w") as f:
        f.write(header)

    print(f"  C header → {output_path}")
    return header


# ---------------------------------------------------------------------------
# 7. Evaluation
# ---------------------------------------------------------------------------
def evaluate(model, X_test, y_test):
    """Print accuracy, confusion matrix, and per-class precision/recall/F1."""
    y_pred = model.predict(X_test, verbose=0)
    y_pred_classes = np.argmax(y_pred, axis=1)
    y_true_classes = np.argmax(y_test, axis=1)

    acc = np.mean(y_pred_classes == y_true_classes)
    print(f"\n{'=' * 60}")
    print(f"  Float32 model test accuracy: {acc:.4f}  ({acc*100:.2f}%)")
    print(f"{'=' * 60}")

    print("\nConfusion matrix (rows=true, cols=predicted):")
    cm = confusion_matrix(y_true_classes, y_pred_classes)
    header_fmt = "{:>14}" * N_CLASSES
    print("     " + header_fmt.format(*CLASS_NAMES))
    for i, row in enumerate(cm):
        print(f"{CLASS_NAMES[i]:>8} " + header_fmt.format(*row))

    print("\nPer-class metrics:")
    print(classification_report(y_true_classes, y_pred_classes, target_names=CLASS_NAMES, digits=4))

    return acc, cm


# ---------------------------------------------------------------------------
# 8. TFLite inference verification
# ---------------------------------------------------------------------------
def verify_tflite(tflite_path, X_test, y_test):
    """Load the .tflite with tf.lite.Interpreter and run inference on test data."""
    print(f"\n{'=' * 60}")
    print("  Verifying TFLite INT8 model inference")
    print(f"{'=' * 60}")

    interpreter = tf.lite.Interpreter(model_path=tflite_path)
    interpreter.allocate_tensors()

    in_det = interpreter.get_input_details()[0]
    out_det = interpreter.get_output_details()[0]

    in_scale, in_zp = in_det["quantization"]
    out_scale, out_zp = out_det["quantization"]

    print(f"  Input:  dtype={in_det['dtype']}, shape={in_det['shape']}, "
          f"scale={in_scale:.6f}, zp={in_zp}")
    print(f"  Output: dtype={out_det['dtype']}, shape={out_det['shape']}, "
          f"scale={out_scale:.6f}, zp={out_zp}")

    y_pred_classes = []
    for i in range(X_test.shape[0]):
        in_f32 = X_test[i : i + 1].astype(np.float32)
        in_i8 = (in_f32 / in_scale + in_zp).astype(np.int8)

        interpreter.set_tensor(in_det["index"], in_i8)
        interpreter.invoke()
        out_i8 = interpreter.get_tensor(out_det["index"])
        out_f32 = (out_i8.astype(np.float32) - out_zp) * out_scale

        y_pred_classes.append(np.argmax(out_f32[0]))

    y_pred_classes = np.array(y_pred_classes)
    y_true_classes = np.argmax(y_test, axis=1)
    acc = np.mean(y_pred_classes == y_true_classes)

    print(f"  TFLite INT8 test accuracy: {acc:.4f}  ({acc * 100:.2f}%)")

    # Compare with float32 model predictions
    print(f"\n  ✓ TFLite model loaded and ran {X_test.shape[0]} inferences successfully.")
    return acc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Train INT8 quantised EEG brain-state detection model for TFLite Micro",
    )
    parser.add_argument("--samples-per-class", type=int, default=SAMPLES_PER_CLASS,
                        help=f"Samples per class (default {SAMPLES_PER_CLASS})")
    parser.add_argument("--max-epochs", type=int, default=200,
                        help="Maximum training epochs (default 200)")
    parser.add_argument("--tflite-output", default=OUTPUT_TFLITE,
                        help=f"Output .tflite path (default {OUTPUT_TFLITE})")
    parser.add_argument("--header-output", default=OUTPUT_HEADER,
                        help=f"Output .h path (default {OUTPUT_HEADER})")
    args = parser.parse_args()

    spc = args.samples_per_class

    print("=" * 60)
    print("  EEG Brain-State Detection — TFLite Micro Trainer")
    print(f"  Classes: {N_CLASSES}, Samples/class: {spc}, "
          f"Total: {spc * N_CLASSES}")
    print(f"  Features: {N_FEATURES} (4 ch × 5 bands), FFT: {N_FFT} @ {FS} Hz")
    print("=" * 60)

    # ---- 1. Generate synthetic EEG -------------------------------------------
    print("\n[1/7] Generating synthetic EEG data ...")
    sos_filters = design_band_filters()
    X_raw, y_onehot = generate_dataset(sos_filters, samples_per_class=spc)
    total = X_raw.shape[0]
    print(f"  Generated {total} samples  |  X shape: {X_raw.shape}  |  "
          f"y shape: {y_onehot.shape}")

    # ---- 2. Extract features ------------------------------------------------
    print("\n[2/7] Extracting band-power features (matching firmware) ...")
    X_feat = extract_features(X_raw)
    print(f"  Features shape: {X_feat.shape}")

    # ---- 3. Train / validation / test split (80 / 10 / 10) -----------------
    print("\n[3/7] Splitting → 70% train / 10% val / 20% test ...")
    X_train, X_test, y_train, y_test = train_test_split(
        X_feat, y_onehot, test_size=0.2, random_state=SEED,
        stratify=np.argmax(y_onehot, axis=1),
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_train, y_train, test_size=0.125, random_state=SEED,
        stratify=np.argmax(y_train, axis=1),
    )
    print(f"  Train: {X_train.shape[0]}  |  Val: {X_val.shape[0]}  |  "
          f"Test: {X_test.shape[0]}")

    # ---- 4. Build & train ---------------------------------------------------
    print("\n[4/7] Building and training model ...")
    model = build_model()
    model.summary()
    train_model(model, X_train, y_train, X_val, y_val, max_epochs=args.max_epochs)

    # ---- 5. Evaluate float model --------------------------------------------
    print("\n[5/7] Evaluating float32 model ...")
    evaluate(model, X_test, y_test)

    # ---- 6. Convert to TFLite INT8 ------------------------------------------
    print("\n[6/7] Converting to TFLite INT8 ...")
    tflite_model = convert_to_tflite_int8(model, X_val, args.tflite_output)

    # ---- 7. Generate C header -----------------------------------------------
    print("\n[7/7] Generating C header ...")
    generate_c_header(tflite_model, args.header_output)

    # ---- 8. Verify TFLite inference -----------------------------------------
    verify_tflite(args.tflite_output, X_test, y_test)

    print(f"\n{'=' * 60}")
    print("  Done.  Model files ready for ESP32-S3 firmware.")
    print(f"  {args.tflite_output}")
    print(f"  {args.header_output}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    sys.exit(main())
