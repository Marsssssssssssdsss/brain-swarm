#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/logging/log.h>
#include <string.h>
#include "uart_protocol.h"

LOG_MODULE_REGISTER(uart_protocol, LOG_LEVEL_DBG);

/* UART device */
#define UART_DEV_NODE DT_NODELABEL(uart1)

static const struct device *uart_dev;
static struct uart_parser parser;
static uart_packet_callback_t packet_callback;

/* Ring buffer for received data */
static uint8_t rx_ring_data[UART_RING_BUF_SIZE];
static struct ring_buf rx_ring;

/* TX buffer for async transmit */
static struct uart_tx_buf tx_buf;
static struct k_mutex tx_mutex;
static struct k_sem tx_sem;

/* Interrupt-driven TX state */
static volatile bool tx_in_progress;

/* Forward declarations */
static void uart_isr(const struct device *dev, void *user_data);

uint8_t uart_crc8_dallas(const uint8_t *data, uint16_t len)
{
    uint8_t crc = 0x00;

    for (uint16_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 0x01) {
                crc = (crc >> 1) ^ 0x8C;
            } else {
                crc >>= 1;
            }
        }
    }

    return crc;
}

static void parse_byte(uint8_t byte)
{
    switch (parser.state) {
    case UART_WAIT_SYNC:
        if (byte == UART_SYNC_BYTE) {
            parser.state = UART_WAIT_TYPE;
            parser.computed_crc = 0;
        }
        break;

    case UART_WAIT_TYPE:
        parser.type = byte;
        parser.computed_crc = uart_crc8_dallas(&parser.type, 1);
        parser.state = UART_WAIT_LENGTH_H;
        break;

    case UART_WAIT_LENGTH_H:
        parser.length = (uint16_t)byte << 8;
        parser.state = UART_WAIT_LENGTH_L;
        break;

    case UART_WAIT_LENGTH_L:
        parser.length |= byte;
        if (parser.length > UART_MAX_PAYLOAD_SIZE) {
            LOG_WRN("Packet too large: %d bytes", parser.length);
            parser.state = UART_WAIT_SYNC;
            break;
        }
        if (parser.length == 0) {
            parser.state = UART_WAIT_CRC;
        } else {
            parser.payload_index = 0;
            parser.state = UART_WAIT_PAYLOAD;
        }
        break;

    case UART_WAIT_PAYLOAD:
        parser.payload[parser.payload_index] = byte;
        parser.payload_index++;
        if (parser.payload_index >= parser.length) {
            parser.state = UART_WAIT_CRC;
        }
        break;

    case UART_WAIT_CRC:
        parser.expected_crc = byte;

        /* Compute CRC over type + length + payload */
        parser.computed_crc = uart_crc8_dallas(&parser.type, 1);
        {
            uint8_t len_buf[2];
            len_buf[0] = (uint8_t)(parser.length >> 8);
            len_buf[1] = (uint8_t)(parser.length & 0xFF);
            parser.computed_crc = uart_crc8_dallas(len_buf, 2);
        }
        parser.computed_crc = uart_crc8_dallas(parser.payload, parser.length);

        if (parser.computed_crc == parser.expected_crc) {
            LOG_DBG("RX packet: type=0x%02X len=%d", parser.type, parser.length);

            if (packet_callback) {
                packet_callback(parser.type, parser.payload, parser.length);
            }
        } else {
            LOG_WRN("CRC mismatch: computed=0x%02X expected=0x%02X",
                    parser.computed_crc, parser.expected_crc);
        }

        parser.state = UART_WAIT_SYNC;
        break;
    }
}

static void uart_isr(const struct device *dev, void *user_data)
{
    ARG_UNUSED(user_data);

    while (uart_irq_rx_ready(dev)) {
        uint8_t byte;
        int ret = uart_fifo_read(dev, &byte, 1);
        if (ret > 0) {
            ring_buf_put(&rx_ring, &byte, 1);
        }
    }

    if (uart_irq_tx_ready(dev) && tx_in_progress) {
        uint8_t byte;
        k_mutex_lock(&tx_mutex, K_NO_WAIT);
        if (tx_buf.tail != tx_buf.head) {
            byte = tx_buf.data[tx_buf.tail];
            tx_buf.tail = (tx_buf.tail + 1) % UART_RING_BUF_SIZE;
            k_mutex_unlock(&tx_mutex);
            uart_fifo_fill(dev, &byte, 1);
        } else {
            tx_in_progress = false;
            k_mutex_unlock(&tx_mutex);
            uart_irq_tx_disable(dev);
            k_sem_give(&tx_sem);
        }
    }
}

static int tx_enqueue(const uint8_t *data, uint16_t len)
{
    int ret;

    k_mutex_lock(&tx_mutex, K_FOREVER);

    for (uint16_t i = 0; i < len; i++) {
        uint16_t next = (tx_buf.head + 1) % UART_RING_BUF_SIZE;
        if (next == tx_buf.tail) {
            k_mutex_unlock(&tx_mutex);
            LOG_ERR("TX buffer full");
            return -ENOBUFS;
        }
        tx_buf.data[tx_buf.head] = data[i];
        tx_buf.head = next;
    }

    ret = 0;

    if (!tx_in_progress) {
        tx_in_progress = true;
        uart_irq_tx_enable(uart_dev);
    }

    k_mutex_unlock(&tx_mutex);
    return ret;
}

int uart_protocol_init(void)
{
    int ret;

    uart_dev = DEVICE_DT_GET(UART_DEV_NODE);
    if (!device_is_ready(uart_dev)) {
        LOG_ERR("UART device not ready");
        return -ENODEV;
    }

    /* Initialize ring buffer */
    ring_buf_init(&rx_ring, sizeof(rx_ring_data), rx_ring_data);

    /* Initialize parser */
    memset(&parser, 0, sizeof(parser));
    parser.state = UART_WAIT_SYNC;

    /* Initialize TX buffer */
    tx_buf.head = 0;
    tx_buf.tail = 0;
    tx_in_progress = false;

    k_mutex_init(&tx_mutex);
    k_sem_init(&tx_sem, 0, 1);

    /* Configure UART: 115200 8N1 */
    uart_irq_callback_set(uart_dev, uart_isr);

    /* Enable RX interrupts */
    uart_irq_rx_enable(uart_dev);

    LOG_INF("UART protocol initialized at 115200 baud");
    return 0;
}

int uart_send_packet(uint8_t type, const uint8_t *payload, uint16_t length)
{
    uint8_t header[4];
    uint8_t crc;
    int ret;

    /* Build header */
    header[0] = UART_SYNC_BYTE;
    header[1] = type;
    header[2] = (uint8_t)(length >> 8);
    header[3] = (uint8_t)(length & 0xFF);

    /* Compute CRC */
    crc = uart_crc8_dallas(&header[1], 1);
    crc = uart_crc8_dallas(&header[2], 2);
    crc = uart_crc8_dallas(payload, length);

    /* Send header */
    ret = tx_enqueue(header, sizeof(header));
    if (ret < 0) return ret;

    /* Send payload */
    if (length > 0) {
        ret = tx_enqueue(payload, length);
        if (ret < 0) return ret;
    }

    /* Send CRC */
    ret = tx_enqueue(&crc, 1);
    if (ret < 0) return ret;

    return 0;
}

int uart_send_eeg_data(const struct eeg_data_payload *eeg)
{
    return uart_send_packet(PKT_TYPE_EEG_DATA,
                            (const uint8_t *)eeg,
                            sizeof(struct eeg_data_payload));
}

int uart_send_log(const char *log_str)
{
    uint16_t len = strlen(log_str);
    if (len > UART_MAX_PAYLOAD_SIZE) {
        len = UART_MAX_PAYLOAD_SIZE;
    }
    return uart_send_packet(PKT_TYPE_LOG, (const uint8_t *)log_str, len);
}

int uart_process(void)
{
    uint8_t byte;
    int processed = 0;

    /* Drain ring buffer into parser */
    while (ring_buf_get(&rx_ring, &byte, 1) > 0) {
        parse_byte(byte);
        processed++;
    }

    return processed;
}

void uart_set_callback(uart_packet_callback_t cb)
{
    packet_callback = cb;
}

bool uart_is_tx_idle(void)
{
    return !tx_in_progress;
}
