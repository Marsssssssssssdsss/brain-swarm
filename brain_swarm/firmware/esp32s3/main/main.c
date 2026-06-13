#include <stdio.h>
#include <string.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "uart_protocol.h"
#include "fft_processor.h"
#include "focus_inference.h"
#include "closed_loop.h"
#include "wifi_ota.h"

static const char *TAG = "neuro_resonator";

/* FreeRTOS task priorities */
#define MAIN_TASK_PRIORITY      5
#define OTA_TASK_PRIORITY       3
#define UART_RX_TASK_PRIORITY   8

/* Main loop interval: 1 second */
#define MAIN_LOOP_MS            1000
#define MAIN_LOOP_TICKS         pdMS_TO_TICKS(MAIN_LOOP_MS)

/* Light sleep timeout */
#define SLEEP_WAKEUP_MS         950

/* State machine phases */
typedef enum {
    PHASE_IDLE,
    PHASE_WAITING_EEG,
    PHASE_PROCESSING_FFT,
    PHASE_RUNNING_INFERENCE,
    PHASE_CLOSED_LOOP,
    PHASE_TX_STATE,
    PHASE_SLEEP
} main_phase_t;

static const char *phase_names[] = {
    "IDLE", "WAIT_EEG", "FFT", "INFERENCE",
    "CLOSED_LOOP", "TX", "SLEEP"
};

/* EEG sample accumulator (4 channels x 256 samples) */
static int32_t eeg_samples[FFT_NUM_CHANNELS][FFT_SAMPLES_PER_SEC];
static int eeg_sample_count = 0;

/* Processing outputs */
static float     band_powers[FFT_NUM_CHANNELS][FFT_NUM_BANDS];
static FocusOutput   focus_out;
static ClosedLoopOutput loop_out;

/* Queue for EEG data from UART callback */
static QueueHandle_t eeg_data_queue = NULL;

/* EEG data message */
typedef struct {
    int32_t samples[FFT_NUM_CHANNELS];
} eeg_sample_msg_t;

/* UART packet callback - called from uart_process_packets */
static void on_uart_packet(uint8_t type, const uint8_t *payload, uint16_t length)
{
    switch (type) {
        case CMD_EEG_DATA: {
            if (length < EEG_PAYLOAD_LEN) {
                ESP_LOGW(TAG, "Short EEG packet: %u bytes", length);
                return;
            }

            /* Parse 4 channels x 24-bit little-endian samples */
            eeg_sample_msg_t msg;
            for (int ch = 0; ch < FFT_NUM_CHANNELS; ch++) {
                int32_t sample = 0;
                sample |= (int32_t)payload[ch * 3 + 0];
                sample |= (int32_t)payload[ch * 3 + 1] << 8;
                sample |= (int32_t)payload[ch * 3 + 2] << 16;
                /* Sign-extend 24-bit to 32-bit */
                if (sample & 0x00800000)
                    sample |= 0xFF000000;
                msg.samples[ch] = sample;
            }

            /* Send to processing queue (non-blocking) */
            if (eeg_data_queue) {
                xQueueSend(eeg_data_queue, &msg, 0);
            }
            break;
        }

        case CMD_LOG: {
            if (length > 0) {
                char log_buf[UART_MAX_PAYLOAD + 1];
                uint16_t copy_len = (length < UART_MAX_PAYLOAD) ? length : UART_MAX_PAYLOAD;
                memcpy(log_buf, payload, copy_len);
                log_buf[copy_len] = '\0';
                ESP_LOGI(TAG, "nRF5340 log: %s", log_buf);
            }
            break;
        }

        case CMD_PING: {
            uart_send(CMD_PONG, NULL, 0);
            break;
        }

        case CMD_SHUTDOWN: {
            ESP_LOGW(TAG, "Shutdown command received from nRF5340");
            closed_loop_reset_timer();
            break;
        }

        case CMD_HEARTBEAT: {
            /* Reset comms timer on heartbeat */
            closed_loop_reset_timer();
            break;
        }

        default:
            ESP_LOGD(TAG, "Unknown packet type: 0x%02X", type);
            break;
    }
}

static void process_eeg_sample(const eeg_sample_msg_t *msg)
{
    if (eeg_sample_count >= FFT_SAMPLES_PER_SEC) {
        return;
    }

    for (int ch = 0; ch < FFT_NUM_CHANNELS; ch++) {
        eeg_samples[ch][eeg_sample_count] = msg->samples[ch];
    }
    eeg_sample_count++;
}

static void run_main_cycle(void)
{
    main_phase_t phase = PHASE_WAITING_EEG;

    /* Check if we have enough samples */
    if (eeg_sample_count < FFT_SAMPLES_PER_SEC) {
        /* Wait for more UART data */
        eeg_sample_msg_t msg;
        TickType_t timeout = pdMS_TO_TICKS(MAIN_LOOP_MS - 50);

        if (xQueueReceive(eeg_data_queue, &msg, timeout) == pdTRUE) {
            process_eeg_sample(&msg);
        }

        /* Also process any other queued samples */
        while (xQueueReceive(eeg_data_queue, &msg, 0) == pdTRUE) {
            process_eeg_sample(&msg);
        }

        if (eeg_sample_count < FFT_SAMPLES_PER_SEC) {
            /* Not enough data yet - log every 10 seconds */
            static int log_counter = 0;
            if (++log_counter % 10 == 0) {
                ESP_LOGD(TAG, "Collecting EEG: %d/%d samples",
                         eeg_sample_count, FFT_SAMPLES_PER_SEC);
            }
            return;
        }
    }

    /* --- PHASE: FFT Processing --- */
    phase = PHASE_PROCESSING_FFT;
    fft_process(eeg_samples, band_powers);

    /* --- PHASE: TFLite Inference --- */
    phase = PHASE_RUNNING_INFERENCE;
    focus_run(band_powers, &focus_out);

    char state_name[16];
    focus_get_state_name(focus_out.state, state_name, sizeof(state_name));
    ESP_LOGI(TAG, "Inference: %s (score=%.1f, conf=%.2f)",
             state_name, focus_out.score, focus_out.confidence);

    /* --- PHASE: Closed-Loop Rules --- */
    phase = PHASE_CLOSED_LOOP;
    closed_loop_process(&focus_out, &loop_out);

    /* --- PHASE: Send result to nRF5340 --- */
    phase = PHASE_TX_STATE;
    uint8_t state_byte   = (uint8_t)focus_out.state;
    uint8_t score_byte   = (uint8_t)(loop_out.focus_score > 100.0f ? 100 :
                          (uint8_t)loop_out.focus_score);
    uint8_t current_byte = (uint8_t)(loop_out.current_ma * 10.0f + 0.5f);
    uint8_t mode_byte    = (uint8_t)loop_out.stim_mode;

    uart_send_focus_state(state_byte, score_byte, current_byte, mode_byte);

    /* Log state to console */
    ESP_LOGI(TAG, "TX->nRF: state=%s score=%d current=%dmA mode=%d",
             state_name, score_byte, current_byte, mode_byte);

    /* Reset sample buffer for next cycle */
    eeg_sample_count = 0;

    /* --- PHASE: Light sleep --- */
    phase = PHASE_SLEEP;
    esp_light_sleep_start();
}

static void main_task(void *arg)
{
    while (1) {
        run_main_cycle();

        /* Process any pending UART packets */
        uart_process_packets(on_uart_packet);

        /* Safety check */
        if (closed_loop_safety_check()) {
            ESP_LOGW(TAG, "Safety check triggered - resetting stimulation");
            closed_loop_reset_timer();
            uart_send(CMD_SHUTDOWN, NULL, 0);
        }

        /* Yield to allow other tasks */
        taskYIELD();
    }
}

static void wifi_ota_task(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(5000));

    wifi_ota_init();

    /* Quick OTA check at boot */
    int update_avail = wifi_ota_check_update(NULL);
    if (update_avail > 0) {
        ESP_LOGI(TAG, "OTA update available, downloading...");
        char url[256];
        snprintf(url, sizeof(url), "%sneuro_resonator_v%s.bin",
                 OTA_URL_BASE, wifi_ota_get_version());
        wifi_ota_start(url);
    }

    /* Task exits after OTA check; OTA can be triggered later via command */
    vTaskDelete(NULL);
}

void app_main(void)
{
    ESP_LOGI(TAG, "NeuroResonator ESP32-S3 AI Domain v%s", wifi_ota_get_version());
    ESP_LOGI(TAG, "System Info: CPU@%dMHz, Free heap=%d",
             esp_clk_cpu_freq() / 1000000, esp_get_free_heap_size());

    /* 1. Initialize NVS */
    esp_err_t nvs_err = nvs_flash_init();
    if (nvs_err == ESP_ERR_NVS_NO_FREE_PAGES ||
        nvs_err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS corrupted, erasing...");
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_err);
    ESP_LOGI(TAG, "NVS initialized");

    /* 2. Initialize UART to nRF5340 */
    uart_init();
    ESP_LOGI(TAG, "UART initialized");

    /* 3. Create EEG data queue */
    eeg_data_queue = xQueueCreate(32, sizeof(eeg_sample_msg_t));
    if (!eeg_data_queue) {
        ESP_LOGE(TAG, "Failed to create EEG queue");
        return;
    }
    ESP_LOGI(TAG, "EEG data queue created");

    /* 4. Initialize FFT processor */
    fft_init();
    ESP_LOGI(TAG, "FFT processor initialized");

    /* 5. Initialize TFLite interpreter */
    int inf_ret = focus_init();
    if (inf_ret != 0) {
        ESP_LOGW(TAG, "TFLite init failed, will use heuristic fallback");
    } else {
        ESP_LOGI(TAG, "TFLite inference engine initialized");
    }

    /* 6. Initialize closed-loop rule engine */
    closed_loop_init();
    ESP_LOGI(TAG, "Closed-loop engine initialized");

    /* 7. Start main processing task */
    xTaskCreatePinnedToCore(
        main_task,
        "neuro_main",
        4096,
        NULL,
        MAIN_TASK_PRIORITY,
        NULL,
        1
    );
    ESP_LOGI(TAG, "Main task started on core 1");

    /* 8. Start WiFi OTA check task (on core 0) */
    xTaskCreatePinnedToCore(
        wifi_ota_task,
        "wifi_ota",
        4096,
        NULL,
        OTA_TASK_PRIORITY,
        NULL,
        0
    );
    ESP_LOGI(TAG, "WiFi OTA task started on core 0");

    /* Main loop is now managed by RTOS tasks */
    ESP_LOGI(TAG, "System ready, entering main loop");
}
