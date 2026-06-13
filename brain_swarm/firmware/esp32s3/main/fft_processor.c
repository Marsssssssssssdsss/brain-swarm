#include <string.h>
#include <math.h>
#include "esp_log.h"
#include "fft_processor.h"
#include "arm_math.h"

static const char *TAG = "fft_proc";

/* Precomputed Hanning window (512 points) */
static float32_t hanning_window[FFT_SIZE];
static int hanning_initialized = 0;

/* CMSIS-DSP FFT instance for real FFT */
static arm_rfft_fast_instance_f32 fft_inst;

/* Per-channel buffers */
static float32_t ch_float[FFT_SIZE];
static float32_t fft_out[FFT_SIZE];

/* Band frequency bin ranges */
static const struct {
    int start_bin;
    int end_bin;
} band_bins[FFT_NUM_BANDS] = {
    { FFT_BIN_START_DELTA,  FFT_BIN_END_DELTA  },  /* Delta  0.5-4 Hz */
    { FFT_BIN_START_THETA,  FFT_BIN_END_THETA  },  /* Theta  4-8 Hz */
    { FFT_BIN_START_ALPHA,  FFT_BIN_END_ALPHA  },  /* Alpha  8-13 Hz */
    { FFT_BIN_START_BETA,   FFT_BIN_END_BETA   },  /* Beta   13-30 Hz */
    { FFT_BIN_START_GAMMA,  FFT_BIN_END_GAMMA  },  /* Gamma  30-45 Hz */
};

void fft_init(void)
{
    if (hanning_initialized)
        return;

    /* Precompute Hanning window */
    for (int i = 0; i < FFT_SIZE; i++) {
        float32_t arg = 2.0f * M_PI * i / (FFT_SIZE - 1);
        hanning_window[i] = 0.5f * (1.0f - cosf(arg));
    }

    /* Initialize CMSIS-DSP real FFT for 512-point */
    arm_rfft_fast_init_f32(&fft_inst, FFT_SIZE);

    hanning_initialized = 1;
    ESP_LOGI(TAG, "FFT initialized: %d-point FFT, %.3f Hz resolution",
             FFT_SIZE, (float)FFT_SAMPLE_RATE / FFT_SIZE);
}

void fft_process(const int32_t samples[FFT_NUM_CHANNELS][FFT_SAMPLES_PER_SEC],
                 float band_powers[FFT_NUM_CHANNELS][FFT_NUM_BANDS])
{
    if (!hanning_initialized) {
        fft_init();
    }

    for (int ch = 0; ch < FFT_NUM_CHANNELS; ch++) {

        /* Copy 256 samples to float buffer, zero-pad to 512 */
        memset(ch_float, 0, sizeof(ch_float));
        for (int i = 0; i < FFT_SAMPLES_PER_SEC; i++) {
            ch_float[i] = (float32_t)samples[ch][i];
        }

        /* Apply Hanning window */
        for (int i = 0; i < FFT_SAMPLES_PER_SEC; i++) {
            ch_float[i] *= hanning_window[i];
        }

        /* Perform real FFT (output is packed: Re[0], Re[1..N/2-1], Im[1..N/2-1], Re[N/2]) */
        arm_rfft_fast_f32(&fft_inst, ch_float, fft_out, 0);

        /* Extract magnitude spectrum for bins 0..FFT_SIZE/2 */
        /* fft_out[0] = DC (bin 0 real), fft_out[FFT_SIZE/2] = Nyquist (bin N/2 real) */
        /* For bins 1..N/2-1: real = fft_out[i], imag = fft_out[FFT_SIZE - i] */

        float32_t mag_sq[FFT_SIZE / 2 + 1];

        /* DC bin */
        mag_sq[0] = fft_out[0] * fft_out[0];

        /* Bins 1..N/2-1 */
        for (int i = 1; i < FFT_SIZE / 2; i++) {
            float32_t re = fft_out[i];
            float32_t im = fft_out[FFT_SIZE - i];
            mag_sq[i] = re * re + im * im;
        }

        /* Nyquist bin */
        mag_sq[FFT_SIZE / 2] = fft_out[FFT_SIZE / 2] * fft_out[FFT_SIZE / 2];

        /* Compute band powers */
        for (int band = 0; band < FFT_NUM_BANDS; band++) {
            float32_t power = 0.0f;
            int start = band_bins[band].start_bin;
            int end   = band_bins[band].end_bin;

            for (int bin = start; bin <= end && bin <= FFT_SIZE / 2; bin++) {
                power += mag_sq[bin];
            }

            /* Convert to dB scale: 10 * log10(power + epsilon) */
            if (power < 1e-12f)
                power = 1e-12f;
            band_powers[ch][band] = 10.0f * log10f(power);
        }
    }

    ESP_LOGD(TAG, "FFT processed %d channels, %d bands each",
             FFT_NUM_CHANNELS, FFT_NUM_BANDS);
}

const char* fft_band_name(int band)
{
    static const char *names[] = { "Delta", "Theta", "Alpha", "Beta", "Gamma" };
    if (band >= 0 && band < FFT_NUM_BANDS)
        return names[band];
    return "Unknown";
}
