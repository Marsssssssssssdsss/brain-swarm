#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>
#include <string.h>
#include "ads1299.h"

LOG_MODULE_REGISTER(ads1299, LOG_LEVEL_DBG);

/* SPI device and configuration */
static const struct device *ads1299_spi;
static struct spi_config spi_cfg;
static struct spi_buf_set tx_buf_set;
static struct spi_buf_set rx_buf_set;

/* GPIO for DRDY, CS, RESET, PWDN, START */
static const struct device *gpio_dev;
static struct gpio_callback drdy_callback;

#define SPI_DEV_NODE    DT_NODELABEL(spi3)
#define GPIO_DEV_NODE   DT_NODELABEL(gpio0)

#define PIN_CS          5
#define PIN_DRDY        6
#define PIN_RESET       7
#define PIN_START       8
#define PIN_PWDN        9

static struct k_sem data_ready_sem;
static volatile bool drdy_asserted;
static bool continuous_mode;
static uint8_t num_channels;
static float channel_gain[8];

/* Forward static declarations */
static int spi_write_read(const uint8_t *tx_data, uint8_t *rx_data, uint16_t len);
static int spi_write(const uint8_t *data, uint16_t len);

static void drdy_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(dev);
    ARG_UNUSED(cb);
    ARG_UNUSED(pins);
    drdy_asserted = true;
    k_sem_give(&data_ready_sem);
}

static int gpio_setup(void)
{
    int ret;

    gpio_dev = DEVICE_DT_GET(GPIO_DEV_NODE);
    if (!device_is_ready(gpio_dev)) {
        LOG_ERR("GPIO device not ready");
        return -ENODEV;
    }

    /* Configure CS as output, initially high */
    ret = gpio_pin_configure(gpio_dev, PIN_CS, GPIO_OUTPUT_ACTIVE);
    if (ret < 0) return ret;
    gpio_pin_set(gpio_dev, PIN_CS, 1);

    /* DRDY as input with interrupt */
    ret = gpio_pin_configure(gpio_dev, PIN_DRDY, GPIO_INPUT | GPIO_PULL_UP);
    if (ret < 0) return ret;
    ret = gpio_pin_interrupt_configure(gpio_dev, PIN_DRDY, GPIO_INT_EDGE_FALLING);
    if (ret < 0) return ret;
    gpio_init_callback(&drdy_callback, drdy_isr, BIT(PIN_DRDY));
    ret = gpio_add_callback(gpio_dev, &drdy_callback);
    if (ret < 0) return ret;

    /* RESET as output, high */
    ret = gpio_pin_configure(gpio_dev, PIN_RESET, GPIO_OUTPUT_ACTIVE);
    if (ret < 0) return ret;
    gpio_pin_set(gpio_dev, PIN_RESET, 1);

    /* START as output, low */
    ret = gpio_pin_configure(gpio_dev, PIN_START, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) return ret;

    /* PWDN as output, high (not in power-down) */
    ret = gpio_pin_configure(gpio_dev, PIN_PWDN, GPIO_OUTPUT_ACTIVE);
    if (ret < 0) return ret;

    LOG_DBG("GPIO setup complete");
    return 0;
}

static int spi_setup(void)
{
    ads1299_spi = DEVICE_DT_GET(SPI_DEV_NODE);
    if (!device_is_ready(ads1299_spi)) {
        LOG_ERR("SPI device not ready");
        return -ENODEV;
    }

    spi_cfg.frequency = 4000000;
    spi_cfg.operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB |
                        SPI_MODE_CPHA | SPI_MODE_CPOL;
    spi_cfg.slave = 0;
    spi_cfg.cs = NULL;

    LOG_DBG("SPI setup complete at 4MHz");
    return 0;
}

static void spi_cs_select(void)
{
    gpio_pin_set(gpio_dev, PIN_CS, 0);
    k_busy_wait(1);
}

static void spi_cs_deselect(void)
{
    k_busy_wait(1);
    gpio_pin_set(gpio_dev, PIN_CS, 1);
}

static int spi_write_read(const uint8_t *tx_data, uint8_t *rx_data, uint16_t len)
{
    int ret;
    struct spi_buf tx_buf = { .buf = (void *)tx_data, .len = len };
    struct spi_buf rx_buf = { .buf = (void *)rx_data, .len = len };

    tx_buf_set.buffers = &tx_buf;
    tx_buf_set.count = 1;
    rx_buf_set.buffers = &rx_buf;
    rx_buf_set.count = 1;

    spi_cs_select();
    ret = spi_transceive(ads1299_spi, &spi_cfg, &tx_buf_set, &rx_buf_set);
    spi_cs_deselect();

    return ret;
}

static int spi_write(const uint8_t *data, uint16_t len)
{
    return spi_write_read(data, NULL, len);
}

int ads1299_read_register(uint8_t reg, uint8_t *value)
{
    uint8_t tx[3];
    uint8_t rx[3];
    int ret;

    tx[0] = ADS1299_CMD_RREG | reg;
    tx[1] = 0x00;
    tx[2] = 0x00;

    ret = spi_write_read(tx, rx, 3);
    if (ret < 0) {
        LOG_ERR("Failed to read register 0x%02X", reg);
        return ret;
    }

    *value = rx[2];
    return 0;
}

int ads1299_write_register(uint8_t reg, uint8_t value)
{
    uint8_t tx[3];
    uint8_t rx[3];
    int ret;

    tx[0] = ADS1299_CMD_WREG | reg;
    tx[1] = 0x00;
    tx[2] = value;

    ret = spi_write_read(tx, rx, 3);
    if (ret < 0) {
        LOG_ERR("Failed to write register 0x%02X = 0x%02X", reg, value);
        return ret;
    }

    LOG_DBG("Reg 0x%02X = 0x%02X", reg, value);
    return 0;
}

static int ads1299_send_command(uint8_t cmd)
{
    return spi_write(&cmd, 1);
}

int ads1299_reset(void)
{
    int ret;

    /* Toggle RESET pin low for at least 2μs */
    ret = gpio_pin_set(gpio_dev, PIN_RESET, 0);
    if (ret < 0) return ret;
    k_busy_wait(10);
    ret = gpio_pin_set(gpio_dev, PIN_RESET, 1);
    if (ret < 0) return ret;

    /* Wait for reset recovery: tPOR = 2^18 / fCLK ≈ 2.1ms at 4MHz internal */
    k_sleep(K_MSEC(10));

    /* Send SDATAC to ensure we're in command mode */
    ret = ads1299_send_command(ADS1299_CMD_SDATAC);
    if (ret < 0) return ret;

    k_sleep(K_MSEC(1));
    LOG_DBG("ADS1299 reset complete");
    return 0;
}

int ads1299_init(const struct ads1299_config *cfg)
{
    int ret;
    uint8_t val;
    uint8_t id_reg;

    if (!cfg) {
        return -EINVAL;
    }

    k_sem_init(&data_ready_sem, 0, 1);
    drdy_asserted = false;
    continuous_mode = false;
    num_channels = cfg->num_channels > ADS1299_NUM_CHANNELS ?
                   ADS1299_NUM_CHANNELS : cfg->num_channels;

    memcpy(channel_gain, cfg->gain, sizeof(channel_gain));

    ret = gpio_setup();
    if (ret < 0) return ret;

    ret = spi_setup();
    if (ret < 0) return ret;

    /* Power-up sequence */
    gpio_pin_set(gpio_dev, PIN_PWDN, 1);
    k_sleep(K_MSEC(10));

    ret = ads1299_reset();
    if (ret < 0) return ret;

    /* Verify device ID */
    ret = ads1299_read_register(ADS1299_REG_ID, &id_reg);
    if (ret < 0) return ret;
    LOG_DBG("ADS1299 ID: 0x%02X", id_reg);

    /* Check ID: bits 7-3 contain device ID, should be 0b10010 for ADS1299 */
    if ((id_reg & 0xF8) != 0x90) {
        LOG_ERR("Unexpected ADS1299 ID: 0x%02X", id_reg);
        return -ENODEV;
    }

    /* Configure CONFIG1: 250 SPS, internal oscillator */
    val = (cfg->sampling_rate & ADS1299_CONFIG1_DR_MASK) | ADS1299_CONFIG1_CLK_EN;
    ret = ads1299_write_register(ADS1299_REG_CONFIG1, val);
    if (ret < 0) return ret;

    /* Configure CONFIG2: internal test off, lead-off AC signal */
    val = ADS1299_CONFIG2_WCT_CHOP;
    ret = ads1299_write_register(ADS1299_REG_CONFIG2, val);
    if (ret < 0) return ret;

    /* Configure CONFIG3: enable bias, internal reference buffer */
    val = ADS1299_CONFIG3_PDB_REFBUF | ADS1299_CONFIG3_VREF_4V |
          ADS1299_CONFIG3_BIAS_REFBUF | ADS1299_CONFIG3_PDB_BIAS;
    if (cfg->bias_enabled) {
        val |= ADS1299_CONFIG3_BIAS_SENS;
    }
    ret = ads1299_write_register(ADS1299_REG_CONFIG3, val);
    if (ret < 0) return ret;

    /* Configure lead-off detection */
    if (cfg->lead_off_enabled) {
        val = ADS1299_LOFF_FLEADOFF_AC | ADS1299_LOFF_VLEADOFF_6nA;
        ret = ads1299_write_register(ADS1299_REG_LOFF, val);
        if (ret < 0) return ret;
    } else {
        ret = ads1299_write_register(ADS1299_REG_LOFF, 0x00);
        if (ret < 0) return ret;
    }

    /* Configure channel settings */
    for (int i = 0; i < ADS1299_NUM_CHANNELS; i++) {
        val = cfg->channel_mux[i] | cfg->gain[i];
        if (cfg->srb1_enabled) {
            val |= ADS1299_CHnSET_SRB2;
        }
        ret = ads1299_write_register(ADS1299_REG_CH1SET + i, val);
        if (ret < 0) return ret;
    }

    /* Configure SRB1 if enabled */
    if (cfg->srb1_enabled) {
        ret = ads1299_write_register(ADS1299_REG_MISC1, 0x20);
        if (ret < 0) return ret;
    }

    /* Wait for settling */
    k_sleep(K_MSEC(500));

    /* Perform offset calibration */
    ret = ads1299_perform_offset_cal();
    if (ret < 0) {
        LOG_WRN("Offset calibration failed, continuing");
    }

    LOG_INF("ADS1299 initialized: %d channels, %d SPS",
            num_channels, 250 / (1 << (cfg->sampling_rate & 0x07)));

    return 0;
}

int ads1299_start_continuous(void)
{
    int ret;

    /* Stop continuous mode first if active */
    if (continuous_mode) {
        ads1299_stop();
    }

    /* Ensure in command mode */
    ret = ads1299_send_command(ADS1299_CMD_SDATAC);
    if (ret < 0) return ret;
    k_sleep(K_MSEC(1));

    /* Start conversions */
    ret = gpio_pin_set(gpio_dev, PIN_START, 1);
    if (ret < 0) return ret;

    k_sleep(K_MSEC(5));

    /* Enter read-data continuous mode */
    ret = ads1299_send_command(ADS1299_CMD_RDATAC);
    if (ret < 0) return ret;

    continuous_mode = true;
    LOG_DBG("ADS1299 continuous mode started");
    return 0;
}

int ads1299_stop(void)
{
    int ret;

    /* Exit read-data continuous mode */
    if (continuous_mode) {
        ret = ads1299_send_command(ADS1299_CMD_SDATAC);
        if (ret < 0) return ret;
    }

    /* Stop conversions */
    ret = gpio_pin_set(gpio_dev, PIN_START, 0);
    if (ret < 0) return ret;

    ret = ads1299_send_command(ADS1299_CMD_STOP);
    if (ret < 0) return ret;

    continuous_mode = false;
    LOG_DBG("ADS1299 stopped");
    return 0;
}

int ads1299_read_data(struct ads1299_sample *sample)
{
    uint8_t tx_buf[27];
    uint8_t rx_buf[27];
    int ret;
    uint8_t num_bytes = 3 + num_channels * 3; /* Status word + 3 bytes per channel */

    if (!sample) {
        return -EINVAL;
    }

    /* Wait for DRDY with timeout */
    if (!drdy_asserted) {
        ret = k_sem_take(&data_ready_sem, K_MSEC(100));
        if (ret < 0) {
            LOG_WRN("DRDY timeout");
            return -ETIMEDOUT;
        }
    }
    drdy_asserted = false;

    /* Read data: in RDATAC mode, just shift out clocks to get data */
    memset(tx_buf, 0x00, sizeof(tx_buf));
    ret = spi_write_read(tx_buf, rx_buf, num_bytes);
    if (ret < 0) {
        LOG_ERR("SPI read failed: %d", ret);
        return ret;
    }

    /* Parse status word (first 3 bytes) - contains LOFF_STATP and LOFF_STATN */
    /* Unused in current implementation */

    /* Parse channel data: 24-bit twos complement, MSB first */
    for (int ch = 0; ch < num_channels; ch++) {
        int32_t raw = 0;
        uint8_t *ch_data = &rx_buf[3 + ch * 3];

        raw = (int32_t)((uint32_t)ch_data[0] << 16 |
                        (uint32_t)ch_data[1] << 8 |
                        (uint32_t)ch_data[2]);

        /* Sign-extend 24-bit to 32-bit */
        if (raw & 0x800000) {
            raw |= 0xFF000000;
        }

        sample->channels[ch] = raw;
    }

    sample->timestamp = k_cycle_get_32();

    return 0;
}

int ads1299_perform_offset_cal(void)
{
    int ret;

    /* Ensure we're not in RDATAC mode */
    if (continuous_mode) {
        ret = ads1299_send_command(ADS1299_CMD_SDATAC);
        if (ret < 0) return ret;
        k_sleep(K_MSEC(1));
    }

    /* Start conversions (need to be running for offset cal) */
    gpio_pin_set(gpio_dev, PIN_START, 1);
    k_sleep(K_MSEC(100));

    /* Send OFFSETCAL command */
    ret = ads1299_send_command(ADS1299_CMD_OFFSETCAL);
    if (ret < 0) return ret;

    /* Wait for calibration to complete (~8 * tDR = 32ms at 250SPS) */
    k_sleep(K_MSEC(100));

    gpio_pin_set(gpio_dev, PIN_START, 0);

    LOG_DBG("Offset calibration complete");
    return 0;
}

int ads1299_self_test(void)
{
    int ret;
    uint8_t val;

    /* Read ID register to verify SPI communication */
    ret = ads1299_read_register(ADS1299_REG_ID, &val);
    if (ret < 0) return ret;

    if ((val & 0xF8) != 0x90) {
        LOG_ERR("Self-test failed: bad ID 0x%02X", val);
        return -EIO;
    }

    /* Verify CONFIG1 can be written and read back */
    ret = ads1299_read_register(ADS1299_REG_CONFIG1, &val);
    if (ret < 0) return ret;

    ret = ads1299_write_register(ADS1299_REG_CONFIG1, val | 0x01);
    if (ret < 0) return ret;

    uint8_t check;
    ret = ads1299_read_register(ADS1299_REG_CONFIG1, &check);
    if (ret < 0) return ret;

    if ((check & 0x01) != (val & 0x01)) {
        LOG_ERR("Self-test failed: register write/read mismatch");
        return -EIO;
    }

    /* Restore */
    ret = ads1299_write_register(ADS1299_REG_CONFIG1, val);
    if (ret < 0) return ret;

    LOG_DBG("Self-test passed");
    return 0;
}

float ads1299_convert_to_voltage(int32_t raw_code)
{
    /* V = (raw_code * Vref) / (gain * 2^23) */
    float voltage = (float)raw_code * ADS1299_VREF;
    voltage /= (channel_gain[0] > 0 ? channel_gain[0] : 24.0f) * 8388608.0f;
    return voltage;
}
