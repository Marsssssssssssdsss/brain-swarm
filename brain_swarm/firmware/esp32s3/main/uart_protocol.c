#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "uart_protocol.h"

static const char *TAG = "uart_proto";

/* Ring buffer for RX */
typedef struct {
    uint8_t  buf[UART_RING_BUF_SIZE];
    uint16_t head;
    uint16_t tail;
    uint16_t count;
} ring_buf_t;

static ring_buf_t rx_ring;
static SemaphoreHandle_t uart_mutex = NULL;
static uart_packet_callback_t packet_callback = NULL;

/* Parser state machine */
typedef enum {
    PARSE_SYNC,
    PARSE_TYPE,
    PARSE_LENGTH_LOW,
    PARSE_LENGTH_HIGH,
    PARSE_PAYLOAD,
    PARSE_CRC
} parse_state_t;

static parse_state_t parse_state = PARSE_SYNC;
static uint8_t  parse_type;
static uint16_t parse_length;
static uint16_t parse_index;
static uint8_t  parse_payload[UART_MAX_PAYLOAD];

/* CRC8 Dallas/Maxim with 0x8C polynomial, 0x31 init */
static uint8_t crc8_dallas(const uint8_t *data, uint16_t len)
{
    uint8_t crc = 0x31;
    for (uint16_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++) {
            if (crc & 0x80)
                crc = (uint8_t)((crc << 1) ^ 0x8C);
            else
                crc = (uint8_t)(crc << 1);
        }
    }
    return crc;
}

static void ring_buf_init(ring_buf_t *rb)
{
    rb->head = 0;
    rb->tail = 0;
    rb->count = 0;
}

static int ring_buf_push(ring_buf_t *rb, uint8_t byte)
{
    if (rb->count >= UART_RING_BUF_SIZE)
        return -1;
    rb->buf[rb->head] = byte;
    rb->head = (rb->head + 1) % UART_RING_BUF_SIZE;
    rb->count++;
    return 0;
}

static int ring_buf_pop(ring_buf_t *rb, uint8_t *byte)
{
    if (rb->count == 0)
        return -1;
    *byte = rb->buf[rb->tail];
    rb->tail = (rb->tail + 1) % UART_RING_BUF_SIZE;
    rb->count--;
    return 0;
}

static void uart_rx_task(void *arg)
{
    uint8_t buf[64];
    while (1) {
        int len = uart_read_bytes(UART_PORT, buf, sizeof(buf), pdMS_TO_TICKS(10));
        if (len > 0) {
            xSemaphoreTake(uart_mutex, portMAX_DELAY);
            for (int i = 0; i < len; i++) {
                ring_buf_push(&rx_ring, buf[i]);
            }
            xSemaphoreGive(uart_mutex);
        }
    }
}

static void feed_byte(uint8_t byte)
{
    switch (parse_state) {
        case PARSE_SYNC:
            if (byte == UART_SYNC_BYTE) {
                parse_state = PARSE_TYPE;
            }
            break;

        case PARSE_TYPE:
            parse_type = byte;
            parse_state = PARSE_LENGTH_LOW;
            break;

        case PARSE_LENGTH_LOW:
            parse_length = byte;
            parse_state = PARSE_LENGTH_HIGH;
            break;

        case PARSE_LENGTH_HIGH:
            parse_length |= ((uint16_t)byte << 8);
            if (parse_length > UART_MAX_PAYLOAD) {
                parse_state = PARSE_SYNC;
                ESP_LOGW(TAG, "Packet length overflow: %u", parse_length);
                return;
            }
            parse_index = 0;
            if (parse_length == 0) {
                parse_state = PARSE_CRC;
            } else {
                parse_state = PARSE_PAYLOAD;
            }
            break;

        case PARSE_PAYLOAD:
            if (parse_index < parse_length) {
                parse_payload[parse_index++] = byte;
            }
            if (parse_index >= parse_length) {
                parse_state = PARSE_CRC;
            }
            break;

        case PARSE_CRC: {
            uint8_t rcvd_crc = byte;
            uint8_t calc_crc = crc8_dallas((uint8_t[]){parse_type,
                (uint8_t)(parse_length & 0xFF),
                (uint8_t)((parse_length >> 8) & 0xFF)}, 3);
            calc_crc = crc8_dallas(parse_payload, parse_length);
            if (rcvd_crc == calc_crc) {
                if (packet_callback) {
                    packet_callback(parse_type, parse_payload, parse_length);
                }
            } else {
                ESP_LOGW(TAG, "CRC mismatch: rcvd=0x%02X calc=0x%02X",
                         rcvd_crc, calc_crc);
            }
            parse_state = PARSE_SYNC;
            break;
        }

        default:
            parse_state = PARSE_SYNC;
            break;
    }
}

void uart_init(void)
{
    uart_mutex = xSemaphoreCreateMutex();
    if (!uart_mutex) {
        ESP_LOGE(TAG, "Failed to create UART mutex");
        return;
    }

    ring_buf_init(&rx_ring);

    uart_config_t uart_config = {
        .baud_rate           = UART_BAUD_RATE,
        .data_bits           = UART_DATA_BITS,
        .parity              = UART_PARITY,
        .stop_bits           = UART_STOP_BITS,
        .flow_ctrl           = UART_HW_FLOWCTRL_DISABLE,
        .source_clk          = UART_SCLK_DEFAULT,
    };

    ESP_ERROR_CHECK(uart_param_config(UART_PORT, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(UART_PORT, UART_TX_GPIO, UART_RX_GPIO,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_ERROR_CHECK(uart_driver_install(UART_PORT, UART_RX_BUF_SIZE * 2,
                                        UART_TX_BUF_SIZE * 2, 0, NULL, 0));

    xTaskCreatePinnedToCore(uart_rx_task, "uart_rx", 2048, NULL, 10, NULL, 0);

    ESP_LOGI(TAG, "UART initialized on port %d (%d baud)", UART_PORT, UART_BAUD_RATE);
}

void uart_send(uint8_t type, const uint8_t *payload, uint16_t length)
{
    if (length > UART_MAX_PAYLOAD) {
        ESP_LOGE(TAG, "Payload too large: %u", length);
        return;
    }

    uint8_t header[UART_HEADER_SIZE];
    header[0] = UART_SYNC_BYTE;
    header[1] = type;
    header[2] = (uint8_t)(length & 0xFF);
    header[3] = (uint8_t)((length >> 8) & 0xFF);

    uint8_t crc = crc8_dallas(&header[1], 3);
    crc = crc8_dallas(payload, length);

    uint8_t *tx_buf = malloc(UART_HEADER_SIZE + length + UART_CRC_SIZE);
    if (!tx_buf) {
        ESP_LOGE(TAG, "malloc failed for UART tx");
        return;
    }

    memcpy(tx_buf, header, UART_HEADER_SIZE);
    if (length > 0 && payload) {
        memcpy(tx_buf + UART_HEADER_SIZE, payload, length);
    }
    tx_buf[UART_HEADER_SIZE + length] = crc;

    uart_write_bytes(UART_PORT, (const char *)tx_buf,
                     UART_HEADER_SIZE + length + UART_CRC_SIZE);
    uart_wait_tx_done(UART_PORT, pdMS_TO_TICKS(100));
    free(tx_buf);
}

void uart_send_focus_state(uint8_t state, uint8_t score, uint8_t current_ma, uint8_t mode)
{
    uint8_t payload[FOCUS_PAYLOAD_LEN] = { state, score, current_ma, mode };
    uart_send(CMD_FOCUS_STATE, payload, FOCUS_PAYLOAD_LEN);
}

void uart_send_log(const char *message)
{
    uint16_t len = strlen(message);
    if (len > UART_MAX_PAYLOAD)
        len = UART_MAX_PAYLOAD;
    uart_send(CMD_LOG, (const uint8_t *)message, len);
}

void uart_process_packets(uart_packet_callback_t callback)
{
    packet_callback = callback;

    uint8_t byte;
    while (1) {
        xSemaphoreTake(uart_mutex, portMAX_DELAY);
        int available = ring_buf_pop(&rx_ring, &byte);
        xSemaphoreGive(uart_mutex);

        if (available == 0)
            break;

        feed_byte(byte);
    }
}

int uart_get_rx_bytes_available(void)
{
    int count;
    xSemaphoreTake(uart_mutex, portMAX_DELAY);
    count = rx_ring.count;
    xSemaphoreGive(uart_mutex);
    return count;
}
