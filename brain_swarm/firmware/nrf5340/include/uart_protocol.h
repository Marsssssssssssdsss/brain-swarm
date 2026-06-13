#ifndef NEURORESONATOR_UART_PROTOCOL_H
#define NEURORESONATOR_UART_PROTOCOL_H

#include <zephyr/types.h>
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* UART configuration */
#define UART_BAUDRATE    115200
#define UART_DATA_BITS   8
#define UART_PARITY      NRF_UART_PARITY_EXCLUDED
#define UART_STOP_BITS   1

/* Protocol constants */
#define UART_SYNC_BYTE        0xAA
#define UART_MAX_PAYLOAD_SIZE 256
#define UART_RING_BUF_SIZE    512

/* Packet types */
#define PKT_TYPE_EEG_DATA     0x01   /* nRF → ESP: 4ch × 24bit EEG */
#define PKT_TYPE_FOCUS_STATE  0x02   /* ESP → nRF: focus state update */
#define PKT_TYPE_LOG          0x03   /* Bidirectional: log messages */
#define PKT_TYPE_COMMAND      0x04   /* nRF → ESP: system command */
#define PKT_TYPE_ACK          0x05   /* Bidirectional: acknowledgment */
#define PKT_TYPE_ERROR        0x06   /* Bidirectional: error report */

/* EEG data packet payload (12 bytes: 4 channels × 24-bit) */
struct __attribute__((packed)) eeg_data_payload {
    uint8_t channel_0[3];    /* 24-bit twos complement, MSB first */
    uint8_t channel_1[3];
    uint8_t channel_2[3];
    uint8_t channel_3[3];
    uint32_t seq_num;        /* Sequence number to detect drops */
    uint32_t timestamp_ms;   /* Local timestamp */
};

/* Focus state payload from ESP32-S3 */
struct __attribute__((packed)) focus_state_payload {
    uint8_t  state;          /* Focus state enum */
    uint8_t  score;          /* 0-100 focus score */
    uint8_t  current_ma_x10; /* Recommended current in 0.1mA */
    uint8_t  mode;           /* Stimulation mode */
    uint8_t  reserved[4];    /* Future use */
};

/* Focus state enums */
#define FOCUS_STATE_DEEP     0x03
#define FOCUS_STATE_MODERATE 0x02
#define FOCUS_STATE_LIGHT    0x01
#define FOCUS_STATE_NONE     0x00
#define FOCUS_STATE_ERROR    0xFF

/* Stimulation modes */
#define STIM_MODE_OFF        0x00
#define STIM_MODE_CONTINUOUS 0x01
#define STIM_MODE_PULSED     0x02
#define STIM_MODE_ALPHA_SYNC 0x03

/* Generic packet header */
struct __attribute__((packed)) uart_packet {
    uint8_t  sync_byte;      /* 0xAA */
    uint8_t  type;           /* Packet type */
    uint16_t length;         /* Payload length (big-endian) */
    uint8_t  payload[UART_MAX_PAYLOAD_SIZE]; /* Variable payload */
    uint8_t  crc;            /* CRC8 of type + length + payload */
};

/* Parser state machine states */
enum uart_parser_state {
    UART_WAIT_SYNC = 0,
    UART_WAIT_TYPE,
    UART_WAIT_LENGTH_H,
    UART_WAIT_LENGTH_L,
    UART_WAIT_PAYLOAD,
    UART_WAIT_CRC
};

/* Parser context */
struct uart_parser {
    enum uart_parser_state state;
    uint8_t  type;
    uint16_t length;
    uint16_t payload_index;
    uint8_t  payload[UART_MAX_PAYLOAD_SIZE];
    uint8_t  expected_crc;
    uint8_t  computed_crc;
};

/* Packet receive callback type */
typedef void (*uart_packet_callback_t)(uint8_t type, const uint8_t *payload, uint16_t length);

/* Transmit context for async sending */
struct uart_tx_buf {
    uint8_t data[UART_RING_BUF_SIZE];
    uint16_t head;
    uint16_t tail;
};

/* API */
int uart_protocol_init(void);
int uart_send_packet(uint8_t type, const uint8_t *payload, uint16_t length);
int uart_send_eeg_data(const struct eeg_data_payload *eeg);
int uart_send_log(const char *log_str);
int uart_process(void);
void uart_set_callback(uart_packet_callback_t cb);
bool uart_is_tx_idle(void);
uint8_t uart_crc8_dallas(const uint8_t *data, uint16_t len);

#ifdef __cplusplus
}
#endif

#endif /* NEURORESONATOR_UART_PROTOCOL_H */
