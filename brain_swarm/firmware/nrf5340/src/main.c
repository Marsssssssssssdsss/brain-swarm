/**
 * NeuroResonator — nRF5340 Sensor Domain Firmware
 *
 * Architecture:
 *   DRDY @250Hz: ADS1299 SPI DMA read -> buffer 4ch x 24bit
 *   Timer @1Hz:   Send latest EEG frame to ESP32-S3 via UART
 *   UART Rx:      Receive focus state from ESP32-S3 -> update tDCS
 *   BLE:          Notify brain state data to phone app
 *   Safety:       Continuous impedance + overcurrent monitoring
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>
#include <string.h>
#include <math.h>

#include "ads1299.h"
#include "ble_service.h"
#include "dac_control.h"
#include "tdcs_safety.h"
#include "uart_protocol.h"

LOG_MODULE_REGISTER(main, LOG_LEVEL_DBG);

/* =========================================================================
 * Constants
 * ========================================================================= */

#define EEG_SAMPLE_RATE_HZ     250
#define BRAIN_STATE_INTERVAL_MS 1000
#define SAFETY_CHECK_INTERVAL_MS 500
#define ADC_REF_VOLTAGE         4.5f
#define ADC_GAIN                24.0f
#define FOCUS_SCORE_THRESHOLD   60

/* EEG ring buffer depth */
#define EEG_RING_BUF_SIZE       64
#define NUM_EEG_CHANNELS        4

/* =========================================================================
 * Global state
 * ========================================================================= */

/* EEG ring buffer */
static struct ads1299_sample eeg_ring_buf[EEG_RING_BUF_SIZE];
static volatile uint16_t eeg_write_idx;
static volatile uint16_t eeg_read_idx;
static struct k_sem eeg_sem;

/* Latest complete frame for sharing */
static struct ads1299_sample latest_eeg_frame;

/* Focus state from ESP32 */
static volatile struct focus_state_payload current_focus_state;

/* Brain state for BLE */
static struct brain_state_payload brain_state;

/* Control flags */
static volatile bool stim_active;
static volatile bool system_initialized;

/* Thread IDs */
static struct k_thread eeg_thread_data;
static struct k_thread ble_thread_data;
static struct k_thread safety_thread_data;
static struct k_thread uart_rx_thread_data;

/* Stack areas */
K_THREAD_STACK_DEFINE(eeg_stack, 4096);
K_THREAD_STACK_DEFINE(ble_stack, 2048);
K_THREAD_STACK_DEFINE(safety_stack, 2048);
K_THREAD_STACK_DEFINE(uart_rx_stack, 2048);

/* Synchronization */
static struct k_sem uart_tx_sem;

/* =========================================================================
 * ADS1299 configuration
 * ========================================================================= */

static const struct ads1299_config sensor_cfg = {
    .num_channels = 4,
    .gain = {
        ADS1299_CHnSET_GAIN_24,
        ADS1299_CHnSET_GAIN_24,
        ADS1299_CHnSET_GAIN_24,
        ADS1299_CHnSET_GAIN_24,
        0, 0, 0, 0
    },
    .channel_mux = {
        ADS1299_CHnSET_MUX_NORMAL,
        ADS1299_CHnSET_MUX_NORMAL,
        ADS1299_CHnSET_MUX_NORMAL,
        ADS1299_CHnSET_MUX_NORMAL,
        0, 0, 0, 0
    },
    .sampling_rate = ADS1299_CONFIG1_DR_250SPS,
    .srb1_enabled = true,
    .bias_enabled = true,
    .lead_off_enabled = true,
};

/* =========================================================================
 * EEG processing thread
 * ========================================================================= */

static void eeg_thread(void *arg1, void *arg2, void *arg3)
{
    ARG_UNUSED(arg1);
    ARG_UNUSED(arg2);
    ARG_UNUSED(arg3);

    int ret;
    struct ads1299_sample sample;

    LOG_INF("EEG thread started");

    while (1) {
        /* Block until DRDY fires (250Hz) */
        ret = ads1299_read_data(&sample);
        if (ret < 0) {
            if (ret != -ETIMEDOUT) {
                LOG_ERR("ADS1299 read failed: %d", ret);
            }
            continue;
        }

        /* Store in ring buffer */
        uint16_t next_idx = (eeg_write_idx + 1) % EEG_RING_BUF_SIZE;
        if (next_idx != eeg_read_idx) {
            memcpy(&eeg_ring_buf[eeg_write_idx], &sample,
                   sizeof(struct ads1299_sample));
            eeg_write_idx = next_idx;
            k_sem_give(&eeg_sem);
        } else {
            LOG_WRN("EEG ring buffer full, dropping sample");
        }

        /* Update latest frame for other consumers */
        memcpy(&latest_eeg_frame, &sample, sizeof(struct ads1299_sample));
    }
}

/* =========================================================================
 * Brain state computation
 * ========================================================================= */

static void compute_brain_state(struct brain_state_payload *bs)
{
    if (!bs) return;

    int32_t ch_data[NUM_EEG_CHANNELS];

    /* Grab latest sample */
    struct ads1299_sample frame;
    memcpy(&frame, (void *)&latest_eeg_frame, sizeof(frame));

    for (int i = 0; i < NUM_EEG_CHANNELS; i++) {
        ch_data[i] = frame.channels[i];
    }

    /* Compress 24-bit to 16-bit for BLE payload */
    for (int i = 0; i < 4; i++) {
        int32_t val = ch_data[i] >> 8;
        if (val < -32768) val = -32768;
        if (val > 32767) val = 32767;
        bs->channel_data[i * 2] = (uint8_t)(val >> 8);
        bs->channel_data[i * 2 + 1] = (uint8_t)(val & 0xFF);
    }

    /* Estimate band powers using simplified amplitude approach */
    int32_t alpha_sum = 0, beta_sum = 0, theta_sum = 0, delta_sum = 0;

    for (int i = 0; i < NUM_EEG_CHANNELS; i++) {
        int32_t abs_val = abs(ch_data[i]);

        /* Simple frequency proxy based on sample-to-sample variation */
        static int32_t prev_ch[NUM_EEG_CHANNELS];
        int32_t diff = ch_data[i] - prev_ch[i];
        prev_ch[i] = ch_data[i];
        int32_t abs_diff = abs(diff);

        /* Delta (0-4Hz): large amplitude, slow change */
        delta_sum += abs_val;

        /* Theta (4-8Hz): medium amplitude */
        theta_sum += abs_val / 2;

        /* Alpha (8-13Hz): medium amplitude, moderate change */
        alpha_sum += abs_val / 3 + abs_diff;

        /* Beta (13-30Hz): smaller amplitude, faster change */
        beta_sum += abs_diff;
    }

    /* Normalize to 0-255 */
    uint32_t norm = (uint32_t)(delta_sum + theta_sum + alpha_sum + beta_sum);
    if (norm == 0) norm = 1;

    bs->delta_power = (uint8_t)((uint32_t)delta_sum * 255 / norm);
    bs->theta_power = (uint8_t)((uint32_t)theta_sum * 255 / norm);
    bs->alpha_power = (uint8_t)((uint32_t)alpha_sum * 255 / norm);
    bs->beta_power = (uint8_t)((uint32_t)beta_sum * 255 / norm);

    /* Focus score: high alpha + beta ratio indicates focus */
    uint16_t alpha_beta = bs->alpha_power + bs->beta_power;
    if (alpha_beta > 0) {
        bs->focus_score = (uint8_t)((uint32_t)bs->beta_power * 100 / alpha_beta);
        if (bs->focus_score > 100) bs->focus_score = 100;
    } else {
        bs->focus_score = 50;
    }

    bs->current_ma_x10 = (uint8_t)(fabsf(dac_get_current_ma()) * 10.0f);

    uint8_t flags = 0;
    if (stim_active) flags |= 0x01;
    if (safety_ok()) flags |= 0x02;
    bs->status_flags = flags;

    memset(bs->reserved, 0, sizeof(bs->reserved));
}

/* =========================================================================
 * BLE notification thread (1Hz)
 * ========================================================================= */

static void ble_thread(void *arg1, void *arg2, void *arg3)
{
    ARG_UNUSED(arg1);
    ARG_UNUSED(arg2);
    ARG_UNUSED(arg3);

    LOG_INF("BLE thread started");

    while (1) {
        k_sleep(K_MSEC(BRAIN_STATE_INTERVAL_MS));

        if (!system_initialized) continue;

        /* Compute brain state from latest EEG frame */
        compute_brain_state(&brain_state);

        /* Send BLE notification */
        int ret = ble_notify_brain_state(&brain_state);
        if (ret < 0 && ret != -ENOTCONN) {
            LOG_WRN("BLE notify failed: %d", ret);
        }
    }
}

/* =========================================================================
 * Safety monitoring thread
 * ========================================================================= */

static void safety_thread(void *arg1, void *arg2, void *arg3)
{
    ARG_UNUSED(arg1);
    ARG_UNUSED(arg2);
    ARG_UNUSED(arg3);

    LOG_INF("Safety thread started");

    while (1) {
        k_sleep(K_MSEC(SAFETY_CHECK_INTERVAL_MS));

        if (!system_initialized) continue;

        int ret = safety_tick();
        if (ret < 0) {
            LOG_ERR("Safety check failed, emergency stop");
            if (stim_active) {
                dac_set_current_ma(0.0f);
                stim_active = false;
            }
        }
    }
}

/* =========================================================================
 * UART RX processing thread
 * ========================================================================= */

static void on_uart_packet(uint8_t type, const uint8_t *payload, uint16_t length)
{
    switch (type) {
    case PKT_TYPE_FOCUS_STATE: {
        if (length < sizeof(struct focus_state_payload)) {
            LOG_WRN("Short focus state packet: %d bytes", length);
            break;
        }

        struct focus_state_payload *fs = (struct focus_state_payload *)payload;
        memcpy((void *)&current_focus_state, fs, sizeof(struct focus_state_payload));

        LOG_DBG("Focus: state=%d score=%d current=%d mode=%d",
                fs->state, fs->score, fs->current_ma_x10, fs->mode);

        /* Apply closed-loop control */
        if (fs->mode != STIM_MODE_OFF && fs->score > FOCUS_SCORE_THRESHOLD) {
            float target_ma = (float)fs->current_ma_x10 / 10.0f;

            /* Safety check before applying */
            float impedance;
            safety_measure_impedance(&impedance);

            if (safety_check(target_ma, impedance) == 0) {
                if (!stim_active) {
                    safety_start_session();
                    stim_active = true;
                }
                dac_ramp_up(target_ma, RAMP_RATE);

                LOG_INF("Stim ON: %.2f mA (focus=%d)", target_ma, fs->score);
            } else {
                LOG_WRN("Safety block: stim not applied");
            }
        } else {
            if (stim_active) {
                LOG_INF("Stim OFF: focus=%d below threshold", fs->score);
                dac_ramp_down(RAMP_RATE);
                safety_stop_session();
                stim_active = false;
            }
        }
        break;
    }

    case PKT_TYPE_LOG: {
        char log_buf[UART_MAX_PAYLOAD_SIZE + 1];
        uint16_t copy_len = length < UART_MAX_PAYLOAD_SIZE ? length : UART_MAX_PAYLOAD_SIZE;
        memcpy(log_buf, payload, copy_len);
        log_buf[copy_len] = '\0';
        LOG_INF("ESP32: %s", log_buf);
        break;
    }

    case PKT_TYPE_ACK: {
        LOG_DBG("ACK received");
        break;
    }

    default:
        LOG_DBG("Unknown packet type: 0x%02X", type);
        break;
    }
}

static void uart_rx_thread(void *arg1, void *arg2, void *arg3)
{
    ARG_UNUSED(arg1);
    ARG_UNUSED(arg2);
    ARG_UNUSED(arg3);

    LOG_INF("UART RX thread started");

    while (1) {
        k_sleep(K_MSEC(10));

        if (!system_initialized) continue;

        uart_process();
    }
}

/* =========================================================================
 * BLE command handler
 * ========================================================================= */

static void on_ble_command(const struct command_payload *cmd)
{
    if (!cmd) return;

    LOG_DBG("BLE cmd: 0x%02X", cmd->cmd_id);

    switch (cmd->cmd_id) {
    case CMD_ID_START_STIM: {
        if (safety_ok()) {
            float impedance;
            safety_measure_impedance(&impedance);
            if (impedance < MAX_IMPEDANCE_KOHM) {
                safety_start_session();
                stim_active = true;
                float target = (float)cmd->param1 / 10.0f;
                dac_ramp_up(target, RAMP_RATE);
                LOG_INF("Stim started from BLE: %.2f mA", target);
            } else {
                LOG_WRN("BLE stim blocked: high impedance %.1f kOhm", impedance);
            }
        }
        break;
    }

    case CMD_ID_STOP_STIM: {
        if (stim_active) {
            dac_ramp_down(RAMP_RATE);
            safety_stop_session();
            stim_active = false;
            LOG_INF("Stim stopped from BLE");
        }
        break;
    }

    case CMD_ID_SET_CURRENT: {
        float target = (float)cmd->param1 / 10.0f;
        if (target <= MAX_CURRENT_MA) {
            dac_set_current_ma(target);
            LOG_DBG("Current set to %.2f mA from BLE", target);
        }
        break;
    }

    case CMD_ID_SET_MODE: {
        LOG_INF("Mode set: %d", cmd->param1);
        break;
    }

    case CMD_ID_CALIBRATE: {
        ads1299_perform_offset_cal();
        LOG_INF("Offset calibration requested from BLE");
        break;
    }

    case CMD_ID_GET_STATUS: {
        struct brain_state_payload status;
        compute_brain_state(&status);
        ble_notify_brain_state(&status);
        break;
    }

    default:
        LOG_WRN("Unknown BLE command: 0x%02X", cmd->cmd_id);
        break;
    }
}

/* =========================================================================
 * UART periodic send (1Hz EEG data to ESP32)
 * ========================================================================= */

static void send_eeg_to_esp32(void)
{
    struct eeg_data_payload eeg_pkt;
    static uint32_t seq = 0;

    /* Grab latest EEG frame */
    struct ads1299_sample frame;
    memcpy(&frame, (void *)&latest_eeg_frame, sizeof(frame));

    /* Pack 24-bit channel data, MSB first */
    for (int ch = 0; ch < NUM_EEG_CHANNELS; ch++) {
        int32_t val = frame.channels[ch];
        uint8_t *dest;
        switch (ch) {
        case 0: dest = eeg_pkt.channel_0; break;
        case 1: dest = eeg_pkt.channel_1; break;
        case 2: dest = eeg_pkt.channel_2; break;
        default: dest = eeg_pkt.channel_3; break;
        }
        dest[0] = (uint8_t)(val >> 16);
        dest[1] = (uint8_t)(val >> 8);
        dest[2] = (uint8_t)(val & 0xFF);
    }

    eeg_pkt.seq_num = seq++;
    eeg_pkt.timestamp_ms = k_uptime_get();

    uart_send_eeg_data(&eeg_pkt);
}

/* =========================================================================
 * Main
 * ========================================================================= */

void main(void)
{
    int ret;

    LOG_INF("NeuroResonator nRF5340 Sensor Domain starting...");

    /* Initialize semaphores */
    k_sem_init(&eeg_sem, 0, EEG_RING_BUF_SIZE);
    k_sem_init(&uart_tx_sem, 0, 1);

    /* Initialize ring buffer indices */
    eeg_write_idx = 0;
    eeg_read_idx = 0;

    /* Clear state */
    memset(&latest_eeg_frame, 0, sizeof(latest_eeg_frame));
    memset(&current_focus_state, 0, sizeof(current_focus_state));
    memset(&brain_state, 0, sizeof(brain_state));
    stim_active = false;
    system_initialized = false;

    /* ---- Initialize ADS1299 ---- */
    ret = ads1299_init(&sensor_cfg);
    if (ret < 0) {
        LOG_ERR("ADS1299 init failed: %d - system halted", ret);
        return;
    }
    LOG_INF("ADS1299 ready at 250 SPS");

    /* Start continuous conversion */
    ret = ads1299_start_continuous();
    if (ret < 0) {
        LOG_ERR("ADS1299 start continuous failed: %d", ret);
        return;
    }

    /* ---- Initialize DAC8562 ---- */
    ret = dac_init();
    if (ret < 0) {
        LOG_ERR("DAC init failed: %d - stim disabled", ret);
    } else {
        LOG_INF("DAC8562 ready, output 0mA");
    }

    /* ---- Initialize UART protocol to ESP32-S3 ---- */
    ret = uart_protocol_init();
    if (ret < 0) {
        LOG_ERR("UART protocol init failed: %d", ret);
        return;
    }
    uart_set_callback(on_uart_packet);
    LOG_INF("UART ready (115200 baud)");

    /* ---- Initialize BLE ---- */
    ret = ble_service_init();
    if (ret < 0) {
        LOG_ERR("BLE init failed: %d", ret);
        return;
    }
    ble_set_command_callback(on_ble_command);
    LOG_INF("BLE advertising as 'NeuroResonator'");

    /* ---- Initialize safety monitor ---- */
    ret = safety_init();
    if (ret < 0) {
        LOG_ERR("Safety init failed: %d", ret);
        return;
    }
    LOG_INF("Safety monitor initialized");

    /* ---- Create threads ---- */
    k_thread_create(&eeg_thread_data, eeg_stack,
                    K_THREAD_STACK_SIZEOF(eeg_stack),
                    eeg_thread, NULL, NULL, NULL,
                    5, 0, K_NO_WAIT);

    k_thread_create(&ble_thread_data, ble_stack,
                    K_THREAD_STACK_SIZEOF(ble_stack),
                    ble_thread, NULL, NULL, NULL,
                    3, 0, K_NO_WAIT);

    k_thread_create(&safety_thread_data, safety_stack,
                    K_THREAD_STACK_SIZEOF(safety_stack),
                    safety_thread, NULL, NULL, NULL,
                    4, 0, K_NO_WAIT);

    k_thread_create(&uart_rx_thread_data, uart_rx_stack,
                    K_THREAD_STACK_SIZEOF(uart_rx_stack),
                    uart_rx_thread, NULL, NULL, NULL,
                    2, 0, K_NO_WAIT);

    system_initialized = true;

    LOG_INF("=== NeuroResonator operational ===");

    /* Main loop: periodic 1Hz tasks and command dispatch */
    while (1) {
        /* Send EEG data to ESP32 every second */
        send_eeg_to_esp32();

        /* Sleep for 1 second */
        k_sleep(K_MSEC(BRAIN_STATE_INTERVAL_MS));
    }
}
