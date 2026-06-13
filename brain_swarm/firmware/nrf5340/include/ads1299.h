#ifndef NEURORESONATOR_ADS1299_H
#define NEURORESONATOR_ADS1299_H

#include <zephyr/types.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ADS1299 Register Map */
#define ADS1299_REG_ID          0x00
#define ADS1299_REG_CONFIG1     0x01
#define ADS1299_REG_CONFIG2     0x02
#define ADS1299_REG_CONFIG3     0x03
#define ADS1299_REG_LOFF        0x04
#define ADS1299_REG_CH1SET      0x05
#define ADS1299_REG_CH2SET      0x06
#define ADS1299_REG_CH3SET      0x07
#define ADS1299_REG_CH4SET      0x08
#define ADS1299_REG_CH5SET      0x09
#define ADS1299_REG_CH6SET      0x0A
#define ADS1299_REG_CH7SET      0x0B
#define ADS1299_REG_CH8SET      0x0C
#define ADS1299_REG_BIAS_SENSP  0x0D
#define ADS1299_REG_BIAS_SENSN  0x0E
#define ADS1299_REG_LOFF_SENSP  0x0F
#define ADS1299_REG_LOFF_SENSN  0x10
#define ADS1299_REG_LOFF_FLIP   0x11
#define ADS1299_REG_LOFF_STATP  0x12
#define ADS1299_REG_LOFF_STATN  0x13
#define ADS1299_REG_GPIO        0x14
#define ADS1299_REG_MISC1       0x15
#define ADS1299_REG_MISC2       0x16
#define ADS1299_REG_CONFIG4     0x17

/* CONFIG1 register bits */
#define ADS1299_CONFIG1_DAISY_EN    (1 << 7)
#define ADS1299_CONFIG1_CLK_EN      (1 << 6)
#define ADS1299_CONFIG1_DR_MASK     0x07
#define ADS1299_CONFIG1_DR_250SPS   0x06
#define ADS1299_CONFIG1_DR_500SPS   0x05
#define ADS1299_CONFIG1_DR_1000SPS  0x04
#define ADS1299_CONFIG1_DR_2000SPS  0x03
#define ADS1299_CONFIG1_DR_4000SPS  0x02
#define ADS1299_CONFIG1_DR_8000SPS  0x01
#define ADS1299_CONFIG1_DR_16000SPS 0x00

/* CONFIG2 register bits */
#define ADS1299_CONFIG2_WCT_CHOP    (1 << 7)
#define ADS1299_CONFIG2_INT_TEST    (1 << 5)
#define ADS1299_CONFIG2_TEST_FREQ   (1 << 4)
#define ADS1299_CONFIG2_TEST_SIG_MASK 0x0C
#define ADS1299_CONFIG2_TEST_SIG_1HZ  0x00

/* CONFIG3 register bits */
#define ADS1299_CONFIG3_PDB_REFBUF  (1 << 7)
#define ADS1299_CONFIG3_VREF_4V     (1 << 6)
#define ADS1299_CONFIG3_BIAS_MEAS   (1 << 5)
#define ADS1299_CONFIG3_BIAS_REFBUF (1 << 4)
#define ADS1299_CONFIG3_BIAS_MVDD   (1 << 3)
#define ADS1299_CONFIG3_BIAS_SENS   (1 << 2)
#define ADS1299_CONFIG3_BIAS_STAT   (1 << 1)
#define ADS1299_CONFIG3_PDB_BIAS    (1 << 0)

/* LOFF register bits */
#define ADS1299_LOFF_COMPLEN        0x07
#define ADS1299_LOFF_VLEADOFF_MASK  0x18
#define ADS1299_LOFF_VLEADOFF_OFF   (0 << 3)
#define ADS1299_LOFF_VLEADOFF_6nA   (2 << 3)
#define ADS1299_LOFF_VLEADOFF_24nA  (3 << 3)
#define ADS1299_LOFF_VLEADOFF_6uA   (7 << 3)
#define ADS1299_LOFF_FLEADOFF_MASK  0x60
#define ADS1299_LOFF_FLEADOFF_DC    (0 << 5)
#define ADS1299_LOFF_FLEADOFF_AC    (1 << 5)

/* CHnSET register bits */
#define ADS1299_CHnSET_PD           (1 << 7)
#define ADS1299_CHnSET_GAIN_MASK    0x70
#define ADS1299_CHnSET_GAIN_1       (0 << 4)
#define ADS1299_CHnSET_GAIN_2       (1 << 4)
#define ADS1299_CHnSET_GAIN_3       (2 << 4)
#define ADS1299_CHnSET_GAIN_4       (3 << 4)
#define ADS1299_CHnSET_GAIN_6       (4 << 4)
#define ADS1299_CHnSET_GAIN_8       (5 << 4)
#define ADS1299_CHnSET_GAIN_12      (6 << 4)
#define ADS1299_CHnSET_GAIN_24      (7 << 4)
#define ADS1299_CHnSET_SRB2         (1 << 3)
#define ADS1299_CHnSET_MUX_MASK     0x07
#define ADS1299_CHnSET_MUX_NORMAL   0x00
#define ADS1299_CHnSET_MUX_SHORTED  0x01
#define ADS1299_CHnSET_MUX_BIAS_MEAS 0x02
#define ADS1299_CHnSET_MUX_MVDD     0x03
#define ADS1299_CHnSET_MUX_TEMP     0x04
#define ADS1299_CHnSET_MUX_TEST     0x05
#define ADS1299_CHnSET_MUX_BIAS_DRP 0x06
#define ADS1299_CHnSET_MUX_BIAS_DRN 0x07

/* SPI commands */
#define ADS1299_CMD_WAKEUP    0x02
#define ADS1299_CMD_STANDBY   0x04
#define ADS1299_CMD_RESET     0x06
#define ADS1299_CMD_START     0x08
#define ADS1299_CMD_STOP      0x0A
#define ADS1299_CMD_OFFSETCAL 0x1A
#define ADS1299_CMD_RDATAC    0x10
#define ADS1299_CMD_SDATAC    0x11
#define ADS1299_CMD_RDATA     0x12
#define ADS1299_CMD_RREG      0x20
#define ADS1299_CMD_WREG      0x40

/* Number of channels */
#define ADS1299_NUM_CHANNELS 4

/* ADC full-scale range */
#define ADS1299_VREF         4.5f
#define ADS1299_GAIN         24.0f
#define ADS1299_LSB_SIZE     (ADS1299_VREF / (ADS1299_GAIN * 16777216.0f * 0.5f))

/* Configuration structure */
struct ads1299_config {
    uint8_t num_channels;
    uint8_t gain[8];
    uint8_t channel_mux[8];
    uint8_t sampling_rate;
    bool srb1_enabled;
    bool bias_enabled;
    bool lead_off_enabled;
};

/* 24-bit EEG data per channel */
struct ads1299_sample {
    int32_t channels[ADS1299_NUM_CHANNELS];
    uint32_t timestamp;
};

/* Driver API */
int ads1299_init(const struct ads1299_config *cfg);
int ads1299_read_register(uint8_t reg, uint8_t *value);
int ads1299_write_register(uint8_t reg, uint8_t value);
int ads1299_start_continuous(void);
int ads1299_stop(void);
int ads1299_read_data(struct ads1299_sample *sample);
int ads1299_perform_offset_cal(void);
int ads1299_reset(void);
int ads1299_self_test(void);
float ads1299_convert_to_voltage(int32_t raw_code);

#ifdef __cplusplus
}
#endif

#endif /* NEURORESONATOR_ADS1299_H */
