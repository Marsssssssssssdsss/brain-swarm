#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/logging/log.h>
#include <string.h>
#include <math.h>
#include "tdcs_safety.h"
#include "dac_control.h"

LOG_MODULE_REGISTER(tdcs_safety, LOG_LEVEL_DBG);

/* Safety state instance */
static struct safety_state safety;
static const struct device *safety_adc_dev;

/* Impedance measurement GPIO: control test current switch */
#define IMPEDANCE_GPIO_DEV  DT_NODELABEL(gpio0)
#define IMPEDANCE_SWITCH_PIN    10
#define IMPEDANCE_MEAS_ADC_CH   1
#define IMPEDANCE_ADC_RES       12

static const struct device *impedance_gpio;

/* Session tracking */
static int64_t session_start_time;
static bool session_active;

int safety_init(void)
{
    int ret;

    memset(&safety, 0, sizeof(safety));
    safety.last_error = SAFETY_OK;
    safety.stim_active = false;
    safety.safe_stop_asserted = false;
    safety.impedance_ok = true;
    safety.overcurrent_ok = true;
    safety.session_time_ok = true;
    safety.current_current_ma = 0.0f;
    safety.measured_impedance_kohm = 0.0f;
    safety.session_elapsed_sec = 0;
    session_active = false;

    /* Initialize impedance measurement GPIO */
    impedance_gpio = DEVICE_DT_GET(IMPEDANCE_GPIO_DEV);
    if (!device_is_ready(impedance_gpio)) {
        LOG_ERR("Impedance GPIO device not ready");
        return -ENODEV;
    }

    ret = gpio_pin_configure(impedance_gpio, IMPEDANCE_SWITCH_PIN,
                             GPIO_OUTPUT_INACTIVE);
    if (ret < 0) return ret;

    /* Initialize ADC for impedance measurement */
    safety_adc_dev = DEVICE_DT_GET(DT_NODELABEL(adc));
    if (!device_is_ready(safety_adc_dev)) {
        LOG_ERR("Safety ADC device not ready");
        return -ENODEV;
    }

    LOG_INF("Safety monitor initialized");
    return 0;
}

int safety_measure_impedance(float *impedance_kohm)
{
    int ret;
    uint16_t sample;
    int32_t voltage_uv;
    float voltage_v;
    float impedance;

    if (!impedance_kohm) {
        return -EINVAL;
    }

    /* Configure ADC for impedance measurement */
    struct adc_sequence as = {
        .channels    = BIT(IMPEDANCE_MEAS_ADC_CH),
        .buffer      = &sample,
        .buffer_size = sizeof(sample),
        .resolution  = IMPEDANCE_ADC_RES,
        .oversampling = 8,
        .gain        = ADC_GAIN_1_6,
        .reference   = ADC_REF_INTERNAL,
    };

    /* Apply 100nA test current by enabling switch */
    gpio_pin_set(impedance_gpio, IMPEDANCE_SWITCH_PIN, 1);
    k_sleep(K_MSEC(5));  /* Settle time for impedance measurement */

    /* Measure voltage */
    ret = adc_read(safety_adc_dev, &as);
    if (ret < 0) {
        gpio_pin_set(impedance_gpio, IMPEDANCE_SWITCH_PIN, 0);
        LOG_ERR("Impedance ADC read failed: %d", ret);
        return ret;
    }

    /* Disable test current */
    gpio_pin_set(impedance_gpio, IMPEDANCE_SWITCH_PIN, 0);

    /* Convert ADC reading to voltage */
    voltage_uv = (int32_t)sample;
    ret = adc_raw_to_millivolts(ADC_REF_INTERNAL,
                                ADC_GAIN_1_6,
                                IMPEDANCE_ADC_RES,
                                &voltage_uv);
    if (ret < 0) {
        LOG_ERR("Raw to millivolt conversion failed");
        return ret;
    }

    voltage_v = (float)voltage_uv / 1000.0f;

    /* Z = V / I where I = 100nA */
    if (voltage_v < 0.00001f) {
        impedance = 0.0f;
    } else {
        impedance = voltage_v / IMPEDANCE_MEAS_CURRENT / 1000.0f;
    }

    *impedance_kohm = impedance;
    safety.measured_impedance_kohm = impedance;

    LOG_DBG("Impedance: %.1f kOhm (voltage: %.3f V)", impedance, voltage_v);
    return 0;
}

int safety_check(float current_ma, float impedance_kohm)
{
    safety.impedance_ok = true;
    safety.overcurrent_ok = true;
    safety.session_time_ok = true;

    /* Check impedance */
    if (impedance_kohm > MAX_IMPEDANCE_KOHM) {
        LOG_WRN("High impedance: %.1f kOhm (limit: %d kOhm)",
                impedance_kohm, MAX_IMPEDANCE_KOHM);
        safety.last_error = ERR_HIGH_IMPEDANCE;
        safety.impedance_ok = false;
    }

    /* Check overcurrent */
    if (fabsf(current_ma) > MAX_CURRENT_MA) {
        LOG_ERR("Overcurrent: %.2f mA (limit: %.1f mA)",
                current_ma, MAX_CURRENT_MA);
        safety.last_error = ERR_OVERCURRENT;
        safety.overcurrent_ok = false;
    }

    /* Check session timeout */
    if (session_active) {
        int64_t now = k_uptime_get();
        int64_t elapsed_ms = now - session_start_time;
        safety.session_elapsed_sec = (uint32_t)(elapsed_ms / 1000);

        if (safety.session_elapsed_sec >= (MAX_SESSION_MIN * 60)) {
            LOG_WRN("Session timeout: %d seconds", safety.session_elapsed_sec);
            safety.last_error = ERR_SESSION_TIMEOUT;
            safety.session_time_ok = false;

            /* Auto-stop session */
            safety_emergency_stop();
        }
    }

    safety.current_current_ma = current_ma;

    return safety_ok() ? 0 : -EIO;
}

bool safety_ok(void)
{
    if (safety.safe_stop_asserted) {
        return false;
    }

    return safety.impedance_ok && safety.overcurrent_ok && safety.session_time_ok;
}

enum safety_error safety_get_error(void)
{
    return safety.last_error;
}

int safety_tick(void)
{
    float impedance;
    float current;
    int ret;

    /* Measure impedance */
    ret = safety_measure_impedance(&impedance);
    if (ret < 0) {
        LOG_ERR("Failed to measure impedance");
        return ret;
    }

    /* Get current DAC output */
    current = dac_get_current_ma();

    /* Run safety check */
    ret = safety_check(current, impedance);
    if (ret < 0) {
        /* Safety violation - emergency stop */
        safety_emergency_stop();
    }

    return 0;
}

int safety_start_session(void)
{
    if (session_active) {
        return 0;
    }

    session_active = true;
    safety.stim_active = true;
    safety.safe_stop_asserted = false;
    safety.last_error = SAFETY_OK;
    session_start_time = k_uptime_get();

    LOG_INF("Safety session started");
    return 0;
}

int safety_stop_session(void)
{
    session_active = false;
    safety.stim_active = false;
    safety.session_elapsed_sec = 0;

    LOG_INF("Safety session stopped");
    return 0;
}

int safety_emergency_stop(void)
{
    int ret;

    LOG_WRN("EMERGENCY STOP triggered (error: %d)", safety.last_error);

    /* Immediately set DAC to 0 */
    ret = dac_set_current_ma(0.0f);
    if (ret < 0) {
        LOG_ERR("DAC shutdown failed in emergency stop");
    }

    safety.safe_stop_asserted = true;
    safety.stim_active = false;
    session_active = false;

    return 0;
}

void safety_reset_error(void)
{
    safety.last_error = SAFETY_OK;
    safety.safe_stop_asserted = false;
    safety.impedance_ok = true;
    safety.overcurrent_ok = true;
    safety.session_time_ok = true;
}

void safety_get_state(struct safety_state *state)
{
    if (state) {
        memcpy(state, &safety, sizeof(struct safety_state));
    }
}
