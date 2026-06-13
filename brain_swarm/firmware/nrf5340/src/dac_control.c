#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/logging/log.h>
#include <math.h>
#include "dac_control.h"

LOG_MODULE_REGISTER(dac_control, LOG_LEVEL_DBG);

/* SPI device for DAC */
#define DAC_SPI_DEV_NODE DT_NODELABEL(spi2)

static const struct device *dac_spi;
static struct spi_config dac_spi_cfg;

/* DAC GPIO control */
#define DAC_CS_PIN      3
#define DAC_LDAC_PIN    4
#define DAC_CLR_PIN     5
#define DAC_GPIO_DEV    DT_NODELABEL(gpio0)

static const struct device *dac_gpio;

/* Current state */
static float current_output_ma;
static bool internal_ref_enabled;
static struct k_mutex dac_mutex;

/* SPI CS control */
static void dac_cs_select(void)
{
    gpio_pin_set(dac_gpio, DAC_CS_PIN, 0);
    k_busy_wait(1);
}

static void dac_cs_deselect(void)
{
    k_busy_wait(1);
    gpio_pin_set(dac_gpio, DAC_CS_PIN, 1);
}

static int dac_spi_transfer(uint8_t *data, uint16_t len)
{
    struct spi_buf tx_buf = { .buf = data, .len = len };
    struct spi_buf_set tx_set = { .buffers = &tx_buf, .count = 1 };

    dac_cs_select();
    int ret = spi_write(dac_spi, &dac_spi_cfg, &tx_set);
    dac_cs_deselect();

    return ret;
}

static int dac_send_command(uint8_t cmd, uint16_t data)
{
    uint8_t buf[3];

    buf[0] = cmd;
    buf[1] = (uint8_t)(data >> 8);
    buf[2] = (uint8_t)(data & 0xFF);

    return dac_spi_transfer(buf, 3);
}

int dac_init(void)
{
    int ret;

    k_mutex_init(&dac_mutex);

    dac_spi = DEVICE_DT_GET(DAC_SPI_DEV_NODE);
    if (!device_is_ready(dac_spi)) {
        LOG_ERR("DAC SPI device not ready");
        return -ENODEV;
    }

    dac_gpio = DEVICE_DT_GET(DAC_GPIO_DEV);
    if (!device_is_ready(dac_gpio)) {
        LOG_ERR("DAC GPIO device not ready");
        return -ENODEV;
    }

    /* Configure CS as output, high */
    ret = gpio_pin_configure(dac_gpio, DAC_CS_PIN, GPIO_OUTPUT_ACTIVE);
    if (ret < 0) return ret;

    /* Configure LDAC as output, low (update on write) */
    ret = gpio_pin_configure(dac_gpio, DAC_LDAC_PIN, GPIO_OUTPUT_ACTIVE);
    if (ret < 0) return ret;
    gpio_pin_set(dac_gpio, DAC_LDAC_PIN, 0);

    /* Configure CLR as output, high (not in clear) */
    ret = gpio_pin_configure(dac_gpio, DAC_CLR_PIN, GPIO_OUTPUT_ACTIVE);
    if (ret < 0) return ret;
    gpio_pin_set(dac_gpio, DAC_CLR_PIN, 1);

    /* SPI config: 10MHz, mode 1 (CPOL=0, CPHA=1) for DAC8562 */
    dac_spi_cfg.frequency = 10000000;
    dac_spi_cfg.operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) |
                            SPI_TRANSFER_MSB | SPI_MODE_CPHA;
    dac_spi_cfg.slave = 0;
    dac_spi_cfg.cs = NULL;

    /* Reset DAC */
    ret = dac_send_command(DAC_CMD_RESET, 0x0001);
    if (ret < 0) {
        LOG_ERR("DAC reset failed: %d", ret);
        return ret;
    }
    k_sleep(K_MSEC(10));

    /* Enable internal reference (2.5V) */
    ret = dac_send_command(DAC_CMD_INTERNAL_REF, 0x0001);
    if (ret < 0) {
        LOG_ERR("DAC internal ref enable failed: %d", ret);
        return ret;
    }
    internal_ref_enabled = true;
    k_sleep(K_MSEC(10));

    /* Set both DAC outputs to 0 (mid-scale = 0mA) */
    ret = dac_send_command(DAC_CMD_WRITE_UPDATE_N | DAC_CHANNEL_A, 0x8000);
    if (ret < 0) return ret;

    ret = dac_send_command(DAC_CMD_WRITE_UPDATE_N | DAC_CHANNEL_B, 0x8000);
    if (ret < 0) return ret;

    current_output_ma = 0.0f;

    LOG_INF("DAC8562 initialized, internal ref %s", internal_ref_enabled ? "enabled" : "disabled");
    return 0;
}

int dac_set_current_ma(float current_ma)
{
    int ret;

    k_mutex_lock(&dac_mutex, K_FOREVER);

    /* Clamp to safe range */
    if (current_ma > DAC_CURRENT_RANGE_MA) {
        current_ma = DAC_CURRENT_RANGE_MA;
    } else if (current_ma < -DAC_CURRENT_RANGE_MA) {
        current_ma = -DAC_CURRENT_RANGE_MA;
    }

    /* Convert mA to DAC code */
    uint16_t dac_code = DAC_CURRENT_TO_CODE(current_ma);

    /* Write to both channels for differential output */
    /* Channel A = Vref + I*R, Channel B = Vref - I*R */
    uint16_t code_a = dac_code;
    uint16_t code_b = (uint16_t)(0x10000 - dac_code);

    ret = dac_send_command(DAC_CMD_WRITE_UPDATE_N | DAC_CHANNEL_A, code_a);
    if (ret < 0) {
        k_mutex_unlock(&dac_mutex);
        return ret;
    }

    ret = dac_send_command(DAC_CMD_WRITE_UPDATE_N | DAC_CHANNEL_B, code_b);
    if (ret < 0) {
        k_mutex_unlock(&dac_mutex);
        return ret;
    }

    current_output_ma = DAC_CODE_TO_CURRENT(dac_code);

    LOG_DBG("DAC set to %.3f mA (code=0x%04X)", current_output_ma, dac_code);

    k_mutex_unlock(&dac_mutex);
    return 0;
}

int dac_set_channel_ma(uint8_t channel, float current_ma)
{
    int ret;
    uint16_t dac_code;

    k_mutex_lock(&dac_mutex, K_FOREVER);

    if (channel > 1) {
        k_mutex_unlock(&dac_mutex);
        return -EINVAL;
    }

    if (current_ma > DAC_CURRENT_RANGE_MA) {
        current_ma = DAC_CURRENT_RANGE_MA;
    } else if (current_ma < -DAC_CURRENT_RANGE_MA) {
        current_ma = -DAC_CURRENT_RANGE_MA;
    }

    dac_code = DAC_CURRENT_TO_CODE(current_ma);
    ret = dac_send_command(DAC_CMD_WRITE_UPDATE_N | channel, dac_code);

    k_mutex_unlock(&dac_mutex);
    return ret;
}

int dac_ramp_up(float target_ma, float ramp_rate)
{
    float start_ma;
    float step_ma;
    int steps;
    int ret;

    k_mutex_lock(&dac_mutex, K_FOREVER);
    start_ma = current_output_ma;
    k_mutex_unlock(&dac_mutex);

    if (target_ma > DAC_CURRENT_RANGE_MA) {
        target_ma = DAC_CURRENT_RANGE_MA;
    }

    if (ramp_rate > DAC_RAMP_RATE_MAX) {
        ramp_rate = DAC_RAMP_RATE_MAX;
    }

    /* Calculate step size for ramp interval */
    float ramp_time_ms = (fabsf(target_ma - start_ma) / ramp_rate) * 1000.0f;

    if (ramp_time_ms < 1.0f) {
        return dac_set_current_ma(target_ma);
    }

    steps = (int)(ramp_time_ms / (float)DAC_RAMP_INTERVAL_MS);
    if (steps < 1) steps = 1;

    step_ma = (target_ma - start_ma) / (float)steps;

    for (int i = 1; i <= steps; i++) {
        float current_step_ma = start_ma + step_ma * (float)i;
        ret = dac_set_current_ma(current_step_ma);
        if (ret < 0) return ret;

        k_sleep(K_MSEC(DAC_RAMP_INTERVAL_MS));
    }

    return dac_set_current_ma(target_ma);
}

int dac_ramp_down(float ramp_rate)
{
    float start_ma;

    k_mutex_lock(&dac_mutex, K_FOREVER);
    start_ma = current_output_ma;
    k_mutex_unlock(&dac_mutex);

    if (ramp_rate > DAC_RAMP_RATE_MAX) {
        ramp_rate = DAC_RAMP_RATE_MAX;
    }

    return dac_ramp_up(0.0f, ramp_rate);
}

int dac_shutdown(void)
{
    int ret;

    /* Ramp down to 0 first */
    ret = dac_ramp_down(DAC_RAMP_RATE_MAX);
    if (ret < 0) return ret;

    /* Power down both DACs */
    ret = dac_send_command(DAC_CMD_POWER_UP_DOWN, 0x0003);
    if (ret < 0) return ret;

    LOG_INF("DAC powered down");
    return 0;
}

int dac_set_internal_ref(bool enable)
{
    int ret;

    ret = dac_send_command(DAC_CMD_INTERNAL_REF, enable ? 0x0001 : 0x0000);
    if (ret < 0) return ret;

    internal_ref_enabled = enable;
    k_sleep(K_MSEC(10));

    LOG_DBG("Internal ref %s", enable ? "enabled" : "disabled");
    return 0;
}

float dac_get_current_ma(void)
{
    float cur;

    k_mutex_lock(&dac_mutex, K_FOREVER);
    cur = current_output_ma;
    k_mutex_unlock(&dac_mutex);

    return cur;
}
