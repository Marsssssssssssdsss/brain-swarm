#include <string.h>
#include <stdio.h>
#include <math.h>
#include "esp_log.h"
#include "focus_inference.h"
#include "tensorflow/lite/micro/all_ops_resolver.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "tensorflow/lite/version.h"

static const char *TAG = "focus_inf";

/* TFLite globals */
static const tflite::Model *tfl_model = NULL;
static tflite::MicroInterpreter *interpreter = NULL;
static TfLiteTensor *input_tensor = NULL;
static TfLiteTensor *output_tensor = NULL;

/* Static tensor arena for TFLite */
static uint8_t tensor_arena[TENSOR_ARENA_SIZE] __attribute__((aligned(16)));

/* Placeholder model data (~4KB INT8 quantized).
   In production, this is compiled from a .tflite file.
   The model takes 20 float inputs (4ch x 5 bands) quantized to INT8,
   and outputs 8 class softmax probabilities. */
static const unsigned char g_focus_model[] = {
    0x1C, 0x00, 0x00, 0x00, 0x54, 0x46, 0x4C, 0x33, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
};

/* Score mapping table */
static const float state_scores[FOCUS_STATE_COUNT] = {
    FOCUS_SCORE_DROWSY,
    FOCUS_SCORE_RELAXED,
    FOCUS_SCORE_CALM,
    FOCUS_SCORE_ATTENTIVE,
    FOCUS_SCORE_FOCUSED,
    FOCUS_SCORE_DEEP_FOCUS,
    FOCUS_SCORE_HYPERFOCUS,
    FOCUS_SCORE_NOISE
};

/* Fallback heuristic classifier */
static void heuristic_classify(const float band_powers[4][5], FocusOutput *out)
{
    float avg_delta = 0, avg_theta = 0, avg_alpha = 0, avg_beta = 0, avg_gamma = 0;
    float alpha_median = 0;

    for (int ch = 0; ch < 4; ch++) {
        avg_delta += band_powers[ch][0];
        avg_theta += band_powers[ch][1];
        avg_alpha += band_powers[ch][2];
        avg_beta  += band_powers[ch][3];
        avg_gamma += band_powers[ch][4];
    }
    avg_delta /= 4.0f;
    avg_theta /= 4.0f;
    avg_alpha /= 4.0f;
    avg_beta  /= 4.0f;
    avg_gamma /= 4.0f;

    /* Sort alphas for median */
    float alphas[4];
    for (int ch = 0; ch < 4; ch++) alphas[ch] = band_powers[ch][2];
    for (int i = 0; i < 3; i++) {
        for (int j = i+1; j < 4; j++) {
            if (alphas[j] < alphas[i]) {
                float t = alphas[i]; alphas[i] = alphas[j]; alphas[j] = t;
            }
        }
    }
    alpha_median = (alphas[1] + alphas[2]) * 0.5f;

    float theta_beta_ratio = (avg_beta > 0.001f) ? (avg_theta / avg_beta) : 0;
    float delta_beta_ratio = (avg_beta > 0.001f) ? (avg_delta / avg_beta) : 0;

    if (avg_gamma > -5.0f) {
        out->state = FOCUS_STATE_HYPERFOCUS;
        out->score = FOCUS_SCORE_HYPERFOCUS;
        out->confidence = 0.65f;
    } else if (theta_beta_ratio > 1.5f && avg_alpha > alpha_median) {
        out->state = FOCUS_STATE_RELAXED;
        out->score = FOCUS_SCORE_RELAXED;
        out->confidence = 0.70f;
    } else if (delta_beta_ratio > 2.0f && avg_theta < -10.0f) {
        out->state = FOCUS_STATE_DROWSY;
        out->score = FOCUS_SCORE_DROWSY;
        out->confidence = 0.70f;
    } else if (theta_beta_ratio < 0.8f && avg_beta > alpha_median) {
        out->state = FOCUS_STATE_FOCUSED;
        out->score = FOCUS_SCORE_FOCUSED;
        out->confidence = 0.65f;
    } else {
        out->state = FOCUS_STATE_CALM;
        out->score = FOCUS_SCORE_CALM;
        out->confidence = 0.50f;
    }
}

int focus_init(void)
{
    /* Load model from flash memory */
    tfl_model = tflite::GetModel(g_focus_model);
    if (tfl_model->version() != TFLITE_SCHEMA_VERSION) {
        ESP_LOGE(TAG, "Model schema version %d doesn't match expected %d",
                 tfl_model->version(), TFLITE_SCHEMA_VERSION);
        return -1;
    }

    /* Create ops resolver and register all built-in ops */
    static tflite::AllOpsResolver resolver;

    /* Create interpreter */
    static tflite::MicroInterpreter static_interpreter(
        tfl_model, resolver, tensor_arena, TENSOR_ARENA_SIZE);
    interpreter = &static_interpreter;

    /* Allocate tensors */
    TfLiteStatus allocate_status = interpreter->AllocateTensors();
    if (allocate_status != kTfLiteOk) {
        ESP_LOGE(TAG, "Tensor allocation failed: %d", allocate_status);
        return -1;
    }

    /* Get input and output tensors */
    input_tensor  = interpreter->input(0);
    output_tensor = interpreter->output(0);

    if (!input_tensor || !output_tensor) {
        ESP_LOGE(TAG, "Failed to get input/output tensors");
        return -1;
    }

    ESP_LOGI(TAG, "TFLite model loaded: %d inputs, %d outputs, arena=%d",
             interpreter->inputs_size(), interpreter->outputs_size(),
             TENSOR_ARENA_SIZE);

    return 0;
}

int focus_run(const float band_powers[4][5], FocusOutput *out)
{
    if (!interpreter || !input_tensor || !output_tensor) {
        /* Fallback: use heuristic classifier if TFLite not initialized */
        heuristic_classify(band_powers, out);
        return 0;
    }

    /* Flatten band powers into input buffer */
    float input_flat[FOCUS_INPUT_SIZE];
    for (int ch = 0; ch < 4; ch++) {
        for (int b = 0; b < 5; b++) {
            input_flat[ch * 5 + b] = band_powers[ch][b];
        }
    }

    /* Quantize input if model is INT8 */
    if (input_tensor->type == kTfLiteInt8) {
        float scale  = input_tensor->params.scale;
        int   zero_point = input_tensor->params.zero_point;
        int8_t *quant_input = tflite::GetTensorData<int8_t>(input_tensor);

        for (int i = 0; i < FOCUS_INPUT_SIZE; i++) {
            int32_t q = (int32_t)roundf(input_flat[i] / scale + zero_point);
            if (q < -128) q = -128;
            if (q > 127) q = 127;
            quant_input[i] = (int8_t)q;
        }
    } else {
        /* Float input */
        float *float_input = tflite::GetTensorData<float>(input_tensor);
        memcpy(float_input, input_flat, sizeof(input_flat));
    }

    /* Run inference */
    TfLiteStatus invoke_status = interpreter->Invoke();
    if (invoke_status != kTfLiteOk) {
        ESP_LOGW(TAG, "TFLite invoke failed, using heuristic fallback");
        heuristic_classify(band_powers, out);
        return 0;
    }

    /* Dequantize output */
    float output_probs[FOCUS_OUTPUT_SIZE];

    if (output_tensor->type == kTfLiteInt8) {
        float scale       = output_tensor->params.scale;
        int   zero_point  = output_tensor->params.zero_point;
        int8_t *quant_out = tflite::GetTensorData<int8_t>(output_tensor);

        for (int i = 0; i < FOCUS_OUTPUT_SIZE; i++) {
            output_probs[i] = (float)(quant_out[i] - zero_point) * scale;
        }
    } else {
        float *float_out = tflite::GetTensorData<float>(output_tensor);
        memcpy(output_probs, float_out, sizeof(output_probs));
    }

    /* Winner-take-all: find class with highest softmax probability */
    int   winner = 0;
    float max_prob = output_probs[0];

    for (int i = 1; i < FOCUS_OUTPUT_SIZE; i++) {
        if (output_probs[i] > max_prob) {
            max_prob = output_probs[i];
            winner = i;
        }
    }

    /* Sanity check: if all probabilities are very low, use heuristic */
    if (max_prob < 0.1f) {
        ESP_LOGW(TAG, "Low confidence (%f), falling back to heuristic", max_prob);
        heuristic_classify(band_powers, out);
        return 0;
    }

    /* Map state and score */
    out->state      = (focus_state_t)winner;
    out->score      = state_scores[winner];
    out->confidence = max_prob;

    ESP_LOGD(TAG, "Inference: state=%d score=%.1f conf=%.3f",
             winner, out->score, out->confidence);

    return 0;
}

void focus_get_state_name(focus_state_t state, char *buf, size_t buf_size)
{
    static const char *names[] = {
        "Drowsy", "Relaxed", "Calm", "Attentive",
        "Focused", "DeepFocus", "Hyperfocus", "Noise"
    };
    if (state >= 0 && state < FOCUS_STATE_COUNT) {
        strncpy(buf, names[state], buf_size - 1);
        buf[buf_size - 1] = '\0';
    } else {
        snprintf(buf, buf_size, "Unknown(%d)", state);
    }
}
