#ifndef UART_PROTOCOL_H
#define UART_PROTOCOL_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Packet sync byte */
#define UART_SYNC_BYTE      0xAA

/* Packet types */
#define CMD_EEG_DATA        0x01
#define CMD_FOCUS_STATE     0x02
#define CMD_LOG             0x03
#define CMD_PING            0x04
#define CMD_PONG            0x05
#define CMD_SHUTDOWN        0x06
#define CMD_HEARTBEAT       0x07

/* Maximum payload size */
#define UART_MAX_PAYLOAD    256

/* Packet header: sync(1) + type(1) + length(2) = 4 bytes */
#define UART_HEADER_SIZE    4
#define UART_CRC_SIZE       1
#define UART_OVERHEAD       (UART_HEADER_SIZE + UART_CRC_SIZE)

/* EEG data payload: 4 channels x 3 bytes (24-bit) = 12 bytes */
#define EEG_PAYLOAD_LEN     12
#define EEG_NUM_CHANNELS    4
#define EEG_SAMPLE_BYTES    3

/* Focus state payload */
#define FOCUS_PAYLOAD_LEN   4

/* UART buffer sizes */
#define UART_RX_BUF_SIZE    256
#define UART_TX_BUF_SIZE    256
#define UART_RING_BUF_SIZE  256

/* UART pin definitions */
#define UART_PORT           UART_NUM_2
#define UART_TX_GPIO        GPIO_NUM_17
#define UART_RX_GPIO        GPIO_NUM_18
#define UART_BAUD_RATE      115200
#define UART_PARITY         UART_PARITY_DISABLE
#define UART_STOP_BITS      UART_STOP_BITS_1
#define UART_DATA_BITS      UART_DATA_BITS_8

/* Packet structure */
typedef struct __attribute__((packed)) {
    uint8_t  sync;
    uint8_t  type;
    uint16_t length;
    uint8_t  payload[UART_MAX_PAYLOAD];
    uint8_t  crc;
} UartPacket;

/* EEG data payload structure (from nRF5340) */
typedef struct __attribute__((packed)) {
    uint8_t  samples[EEG_NUM_CHANNELS][EEG_SAMPLE_BYTES];
} EegDataPayload;

/* Focus state payload structure (to nRF5340) */
typedef struct __attribute__((packed)) {
    uint8_t  state;
    uint8_t  score;
    uint8_t  current_ma;
    uint8_t  mode;
} FocusStatePayload;

/* Callback for received packets */
typedef void (*uart_packet_callback_t)(uint8_t type, const uint8_t *payload, uint16_t length);

/* UART protocol API */
void uart_init(void);
void uart_send(uint8_t type, const uint8_t *payload, uint16_t length);
void uart_send_focus_state(uint8_t state, uint8_t score, uint8_t current_ma, uint8_t mode);
void uart_send_log(const char *message);
void uart_process_packets(uart_packet_callback_t callback);
int uart_get_rx_bytes_available(void);

#ifdef __cplusplus
}
#endif

#endif /* UART_PROTOCOL_H */
