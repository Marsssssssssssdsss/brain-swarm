#ifndef NEURORESONATOR_BLE_SERVICE_H
#define NEURORESONATOR_BLE_SERVICE_H

#include <zephyr/types.h>
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Custom service UUID: B9580000-6E82-4B8A-BF9C-7A9B1B0F1C3D */
/* Brain state characteristic UUID: B9580001-6E82-4B8A-BF9C-7A9B1B0F1C3D */
/* Command characteristic UUID:     B9580002-6E82-4B8A-BF9C-7A9B1B0F1C3D */
/* Battery characteristic UUID:     B9580003-6E82-4B8A-BF9C-7A9B1B0F1C3D */

#define BRAIN_STATE_UUID_VAL \
    BT_UUID_128_ENCODE(0xB9580001, 0x6E82, 0x4B8A, 0xBF9C, 0x7A9B1B0F1C3D)
#define COMMAND_UUID_VAL \
    BT_UUID_128_ENCODE(0xB9580002, 0x6E82, 0x4B8A, 0xBF9C, 0x7A9B1B0F1C3D)
#define BATTERY_UUID_VAL \
    BT_UUID_128_ENCODE(0xB9580003, 0x6E82, 0x4B8A, 0xBF9C, 0x7A9B1B0F1C3D)
#define SERVICE_UUID_VAL \
    BT_UUID_128_ENCODE(0xB9580000, 0x6E82, 0x4B8A, 0xBF9C, 0x7A9B1B0F1C3D)

/* Brain state notification payload (20 bytes) */
struct brain_state_payload {
    uint8_t channel_data[8];    /* 4 channels × 2 bytes (compressed) */
    uint8_t alpha_power;        /* Alpha band power scaled 0-255 */
    uint8_t beta_power;         /* Beta band power scaled 0-255 */
    uint8_t theta_power;        /* Theta band power scaled 0-255 */
    uint8_t delta_power;        /* Delta band power scaled 0-255 */
    uint8_t focus_score;        /* 0-100 focus score */
    uint8_t current_ma_x10;     /* tDCS current in 0.1mA units */
    uint8_t status_flags;       /* Bit 0: stim_active, Bit 1: safety_ok */
    uint8_t reserved[3];
} __attribute__((packed));

/* Command payload from phone (4 bytes) */
struct command_payload {
    uint8_t cmd_id;
    uint8_t param1;
    uint8_t param2;
    uint8_t param3;
} __attribute__((packed));

/* Command IDs */
#define CMD_ID_START_STIM     0x01
#define CMD_ID_STOP_STIM      0x02
#define CMD_ID_SET_CURRENT    0x03
#define CMD_ID_SET_MODE       0x04
#define CMD_ID_CALIBRATE      0x05
#define CMD_ID_GET_STATUS     0x06

/* Command callback type */
typedef void (*ble_command_callback_t)(const struct command_payload *cmd);

/* API */
int ble_service_init(void);
int ble_notify_brain_state(const struct brain_state_payload *state);
int ble_update_battery_level(uint8_t level_percent);
void ble_set_command_callback(ble_command_callback_t cb);
bool ble_is_connected(void);

#ifdef __cplusplus
}
#endif

#endif /* NEURORESONATOR_BLE_SERVICE_H */
