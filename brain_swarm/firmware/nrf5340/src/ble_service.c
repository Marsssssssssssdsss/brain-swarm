#include <zephyr/kernel.h>
#include <zephyr/types.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/device.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/logging/log.h>
#include <string.h>
#include "ble_service.h"

LOG_MODULE_REGISTER(ble_service, LOG_LEVEL_DBG);

/* Battery ADC configuration */
#define BATTERY_ADC_NODE DT_NODELABEL(adc)
#define BATTERY_ADC_CHANNEL 0
#define BATTERY_ADC_RESOLUTION 12
#define BATTERY_ADC_GAIN ADC_GAIN_1_6
#define BATTERY_ADC_REFERENCE ADC_REF_INTERNAL
#define BATTERY_ADC_ACQUISITION_TIME ADC_ACQ_TIME_DEFAULT
#define BATTERY_VOLTAGE_DIVIDER_RATIO 2.0f
#define BATTERY_FULL_MV 4200
#define BATTERY_EMPTY_MV 3200

static const struct device *adc_dev;
static uint8_t current_battery_level;
static bool ble_connected;
static ble_command_callback_t command_cb;

/* Brain State characteristic */
static struct bt_gatt_attr service_attrs[];

/* CCCD configuration for notifications */
static struct bt_gatt_ccc_cfg brain_state_ccc_cfg[CONFIG_BT_MAX_CONN];

static void brain_state_ccc_cfg_changed(const struct bt_gatt_attr *attr,
                                        uint16_t value)
{
    LOG_DBG("Brain state CCC changed: 0x%04X", value);
}

static ssize_t on_command_write(struct bt_conn *conn,
                                const struct bt_gatt_attr *attr,
                                const void *buf, uint16_t len,
                                uint16_t offset, uint8_t flags)
{
    const struct command_payload *cmd = (const struct command_payload *)buf;

    if (offset > 0) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }

    if (len < sizeof(struct command_payload)) {
        LOG_WRN("Command write too short: %d bytes", len);
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
    }

    LOG_DBG("BLE command: id=0x%02X p1=0x%02X p2=0x%02X p3=0x%02X",
            cmd->cmd_id, cmd->param1, cmd->param2, cmd->param3);

    if (command_cb) {
        command_cb(cmd);
    }

    return len;
}

static ssize_t on_battery_read(struct bt_conn *conn,
                               const struct bt_gatt_attr *attr,
                               void *buf, uint16_t len,
                               uint16_t offset)
{
    return bt_gatt_attr_read(conn, attr, buf, len, offset,
                             &current_battery_level, sizeof(current_battery_level));
}

/* Brain state characteristic value */
static struct brain_state_payload current_brain_state;

/* UUIDs */
static struct bt_uuid_128 brain_state_uuid = BT_UUID_INIT_128(BRAIN_STATE_UUID_VAL);
static struct bt_uuid_128 command_uuid = BT_UUID_INIT_128(COMMAND_UUID_VAL);
static struct bt_uuid_128 battery_uuid = BT_UUID_INIT_128(BATTERY_UUID_VAL);
static struct bt_uuid_128 service_uuid = BT_UUID_INIT_128(SERVICE_UUID_VAL);

/* GATT attribute array */
static struct bt_gatt_attr service_attrs[] = {
    BT_GATT_PRIMARY_SERVICE(&service_uuid),

    BT_GATT_CHARACTERISTIC(&brain_state_uuid,
                           BT_GATT_CHRC_NOTIFY | BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ,
                           NULL, &current_brain_state, NULL),

    BT_GATT_CCC(brain_state_ccc_cfg, brain_state_ccc_cfg_changed),

    BT_GATT_CHARACTERISTIC(&command_uuid,
                           BT_GATT_CHRC_WRITE,
                           BT_GATT_PERM_WRITE,
                           NULL, NULL, NULL),

    BT_GATT_DESCRIPTOR(&command_uuid,
                       BT_GATT_PERM_WRITE,
                       NULL, on_command_write, NULL),

    BT_GATT_CHARACTERISTIC(&battery_uuid,
                           BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ,
                           NULL, NULL, NULL),

    BT_GATT_DESCRIPTOR(&battery_uuid,
                       BT_GATT_PERM_READ,
                       on_battery_read, NULL, NULL),
};

/* GATT service declaration */
static struct bt_gatt_service brain_service = BT_GATT_SERVICE(service_attrs);

/* Connection callbacks */
static void on_connected(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        LOG_ERR("Connection failed: err %d", err);
        return;
    }
    ble_connected = true;
    LOG_INF("BLE connected");
}

static void on_disconnected(struct bt_conn *conn, uint8_t reason)
{
    ble_connected = false;
    LOG_INF("BLE disconnected (reason %d)", reason);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
    .connected = on_connected,
    .disconnected = on_disconnected,
};

/* Advertising data */
static const struct bt_data ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
    BT_DATA_BYTES(BT_DATA_UUID128_ALL,
                  0x00, 0x00, 0x58, 0xB9, 0x82, 0x6E, 0x8A, 0x4B,
                  0xBF, 0x9C, 0x7A, 0x9B, 0x1B, 0x0F, 0x1C, 0x3D),
};

static const struct bt_data sd[] = {
    BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static int adc_setup(void)
{
    int ret;

    adc_dev = DEVICE_DT_GET(DT_NODELABEL(adc));
    if (!device_is_ready(adc_dev)) {
        LOG_ERR("ADC device not ready");
        return -ENODEV;
    }

    return 0;
}

static int read_battery_level(void)
{
    int ret;
    uint16_t sample;
    struct adc_sequence sequence = {
        .channels    = BIT(BATTERY_ADC_CHANNEL),
        .buffer      = &sample,
        .buffer_size = sizeof(sample),
        .resolution  = BATTERY_ADC_RESOLUTION,
        .oversampling = 4,
        .gain        = ADC_GAIN_1_6,
        .reference   = ADC_REF_INTERNAL,
    };

    ret = adc_read(adc_dev, &sequence);
    if (ret < 0) {
        LOG_ERR("ADC read failed: %d", ret);
        return ret;
    }

    /* Convert ADC reading to voltage, account for divider */
    int32_t mv = sample;
    ret = adc_raw_to_millivolts(ADC_REF_INTERNAL,
                                ADC_GAIN_1_6,
                                BATTERY_ADC_RESOLUTION,
                                &mv);
    if (ret < 0) {
        LOG_ERR("Raw to mV conversion failed");
        return ret;
    }

    mv = (int32_t)(mv * BATTERY_VOLTAGE_DIVIDER_RATIO);

    /* Convert to percentage */
    if (mv >= BATTERY_FULL_MV) {
        current_battery_level = 100;
    } else if (mv <= BATTERY_EMPTY_MV) {
        current_battery_level = 0;
    } else {
        current_battery_level = (uint8_t)(((mv - BATTERY_EMPTY_MV) * 100) /
                                           (BATTERY_FULL_MV - BATTERY_EMPTY_MV));
    }

    return 0;
}

int ble_service_init(void)
{
    int ret;

    ret = adc_setup();
    if (ret < 0) return ret;

    ret = bt_enable(NULL);
    if (ret < 0) {
        LOG_ERR("Bluetooth init failed: %d", ret);
        return ret;
    }

    LOG_DBG("Bluetooth initialized");

    /* Register the custom service */
    ret = bt_gatt_service_register(&brain_service);
    if (ret < 0) {
        LOG_ERR("GATT service registration failed: %d", ret);
        return ret;
    }

    LOG_DBG("Brain state service registered");

    /* Start advertising */
    ret = bt_le_adv_start(BT_LE_ADV_CONN_FAST_1, ad, ARRAY_SIZE(ad),
                          sd, ARRAY_SIZE(sd));
    if (ret < 0) {
        LOG_ERR("Advertising failed to start: %d", ret);
        return ret;
    }

    /* Read initial battery level */
    read_battery_level();

    LOG_INF("BLE service ready, advertising as '%s'", CONFIG_BT_DEVICE_NAME);
    return 0;
}

int ble_notify_brain_state(const struct brain_state_payload *state)
{
    if (!state) {
        return -EINVAL;
    }

    if (!ble_connected) {
        return -ENOTCONN;
    }

    memcpy(&current_brain_state, state, sizeof(struct brain_state_payload));

    int ret = bt_gatt_notify(NULL, &service_attrs[1], &current_brain_state,
                             sizeof(struct brain_state_payload));
    if (ret < 0 && ret != -EAGAIN) {
        LOG_ERR("Notify failed: %d", ret);
    }

    return ret;
}

int ble_update_battery_level(uint8_t level_percent)
{
    current_battery_level = level_percent;

    if (ble_connected) {
        bt_gatt_notify(NULL, &service_attrs[4], &current_battery_level,
                       sizeof(current_battery_level));
    }

    return 0;
}

void ble_set_command_callback(ble_command_callback_t cb)
{
    command_cb = cb;
}

bool ble_is_connected(void)
{
    return ble_connected;
}
