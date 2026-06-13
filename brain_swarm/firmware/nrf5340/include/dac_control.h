#ifndef NEURORESONATOR_DAC_CONTROL_H
#define NEURORESONATOR_DAC_CONTROL_H

#include <zephyr/types.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* DAC8562 voltage reference */
#define DAC8562_VREF          2.5f
#define DAC8562_RESOLUTION    65535.0f

/* Current range: ±2mA through 2.5kΩ load with differential output */
/* I_out = (Vout - Vref) / R_load */
/* For ±2mA: Vout ranges from Vref-5V to Vref+5V */
/* DAC equation: dac_code = (desired_current_ma / CURRENT_RANGE_MA) * DAC_RESOLUTION */

#define DAC_CURRENT_RANGE_MA  2.0f
#define DAC_LOAD_RESISTOR     2500.0f   /* 2.5kΩ */
#define DAC_RAMP_RATE_MAX     0.5f      /* mA/s max ramp rate */
#define DAC_RAMP_INTERVAL_MS  50        /* Update interval for ramping */

/* DAC8562 commands */
#define DAC_CMD_WRITE_INPUT_N     0x00  /* Write to input register N */
#define DAC_CMD_UPDATE_DAC_N      0x10  /* Update DAC register N */
#define DAC_CMD_WRITE_UPDATE_N    0x20  /* Write to input N, update all */
#define DAC_CMD_WRITE_UPDATE_BC   0x30  /* Write to input N, update broadcast */
#define DAC_CMD_POWER_UP_DOWN     0x40
#define DAC_CMD_RESET             0x50
#define DAC_CMD_LDAC_REG          0x60
#define DAC_CMD_INTERNAL_REF      0x70

/* DAC channels */
#define DAC_CHANNEL_A  0
#define DAC_CHANNEL_B  1

/* DAC code calculation */
#define DAC_CURRENT_TO_CODE(ma) \
    (uint16_t)(((float)(ma) / DAC_CURRENT_RANGE_MA) * 32768.0f + 32768.0f)

#define DAC_CODE_TO_CURRENT(code) \
    (((float)(code) - 32768.0f) / 32768.0f * DAC_CURRENT_RANGE_MA)

/* API */
int dac_init(void);
int dac_set_current_ma(float current_ma);
int dac_set_channel_ma(uint8_t channel, float current_ma);
int dac_ramp_up(float target_ma, float ramp_rate);
int dac_ramp_down(float ramp_rate);
int dac_shutdown(void);
int dac_set_internal_ref(bool enable);
float dac_get_current_ma(void);

#ifdef __cplusplus
}
#endif

#endif /* NEURORESONATOR_DAC_CONTROL_H */
