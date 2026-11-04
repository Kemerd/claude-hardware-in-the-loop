/**
 * @file link.h
 * @brief The byte-level command / telemetry protocol between a ground station (or any host) and the firmware.
 *
 * The same encoder and decoder are compiled into the firmware, the twin and the hosts, so a scenario that drives
 * the twin with frames drives the real board with the same frames over a UART, USB CDC or radio.
 *
 * Frame:  0xA5 | type | len | payload[len] | crc8
 *         crc8 = CRC-8 (polynomial 0x07, init 0x00, no reflection) over type, len and the payload.
 * Payloads are little-endian; floats are IEEE-754 binary32.
 */
#ifndef HIL_CORE_LINK_H
#define HIL_CORE_LINK_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HIL_LINK_SYNC 0xA5u
#define HIL_LINK_MAX_PAYLOAD 32
#define HIL_LINK_MAX_FRAME (HIL_LINK_MAX_PAYLOAD + 4)

/** @brief Message types. Append new ones; never renumber. */
enum {
    HIL_MSG_ARM = 0x01,        /**< payload: u8 1 = arm (homes, then holds), 0 = disarm (motor off, clears faults) */
    HIL_MSG_SET_POS = 0x02,    /**< payload: f32 joint target, rad */
    HIL_MSG_STOP = 0x03,       /**< payload: none; hold the present position */
    HIL_MSG_TELEM = 0x80       /**< firmware -> host: see hil_telem_t */
};

/** @brief Telemetry payload (firmware -> host), 50 Hz, HIL_TELEM_LEN bytes. */
typedef struct {
    uint8_t state;             /**< hil_core_state_t */
    uint8_t fault;             /**< bit mask of HIL_FAULT_* */
    float q_rad;               /**< joint angle as the firmware measures it */
    float qd_rad_s;
    float i_a;                 /**< measured motor current */
    float vbat_v;
    float t_motor_c;           /**< motor case, from the NTC */
    float t_winding_est_c;     /**< winding, estimated by the firmware's thermal observer */
    float derate;              /**< 1 = full current available, < 1 thermally derated */
} hil_telem_t;

/** @brief Telemetry payload length. */
#define HIL_TELEM_LEN 30

/** @brief Streaming decoder state (one per byte source). */
typedef struct {
    uint8_t buf[HIL_LINK_MAX_FRAME];
    int n;
    uint32_t crc_errors;
    uint32_t frames;
} hil_link_rx_t;

/** @brief CRC-8, polynomial 0x07. */
uint8_t hil_link_crc8(const uint8_t* d, int n);

/**
 * @brief Encode one frame.
 * @return frame length, or -1 when the payload is too long or a pointer is NULL
 */
int hil_link_encode(uint8_t type, const uint8_t* payload, int len, uint8_t* out, int cap);

/**
 * @brief Feed one byte.
 * @param rx       decoder
 * @param byte     next byte from the wire
 * @param type     set when a frame completes
 * @param payload  receives the payload (HIL_LINK_MAX_PAYLOAD bytes)
 * @param len      set to the payload length
 * @return 1 a valid frame completed, 0 need more bytes, -1 a frame failed its CRC (dropped, counted)
 */
int hil_link_feed(hil_link_rx_t* rx, uint8_t byte, uint8_t* type, uint8_t* payload, int* len);

/** @brief Little-endian float helpers (no type punning through pointers: memcpy only). */
void hil_put_f32(uint8_t* p, float v);
float hil_get_f32(const uint8_t* p);

/** @brief Telemetry pack / unpack (fixed HIL_TELEM_LEN payload). */
int hil_telem_pack(const hil_telem_t* t, uint8_t* payload);
int hil_telem_unpack(const uint8_t* payload, int len, hil_telem_t* t);

#ifdef __cplusplus
}
#endif

#endif /* HIL_CORE_LINK_H */
