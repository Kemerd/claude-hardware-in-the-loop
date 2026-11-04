/**
 * @file link.c
 * @brief Frame codec for the command / telemetry link (see link.h).
 */
#include "hil_core/link.h"

#include <string.h>

uint8_t hil_link_crc8(const uint8_t* d, int n)
{
    uint8_t crc = 0;
    if (!d || n <= 0) return crc;
    for (int i = 0; i < n; ++i) {
        crc ^= d[i];
        /* ---- bitwise; a 256-entry table is faster but this runs at 50 Hz on frames of a few bytes */
        for (int b = 0; b < 8; ++b) crc = (uint8_t)((crc & 0x80u) ? (uint8_t)((crc << 1) ^ 0x07u) : (uint8_t)(crc << 1));
    }
    return crc;
}

int hil_link_encode(uint8_t type, const uint8_t* payload, int len, uint8_t* out, int cap)
{
    if (!out || len < 0 || len > HIL_LINK_MAX_PAYLOAD || (len > 0 && !payload) || cap < len + 4) return -1;
    out[0] = HIL_LINK_SYNC;
    out[1] = type;
    out[2] = (uint8_t)len;
    if (len > 0) memcpy(&out[3], payload, (size_t)len);
    out[3 + len] = hil_link_crc8(&out[1], len + 2);
    return len + 4;
}

int hil_link_feed(hil_link_rx_t* rx, uint8_t byte, uint8_t* type, uint8_t* payload, int* len)
{
    if (!rx || !type || !payload || !len) return 0;

    /* ---- hunt for the sync byte */
    if (rx->n == 0 && byte != HIL_LINK_SYNC) return 0;
    rx->buf[rx->n++] = byte;

    /* ---- a length beyond the limit means we locked onto a payload byte: resynchronise */
    if (rx->n == 3 && rx->buf[2] > HIL_LINK_MAX_PAYLOAD) {
        rx->n = 0;
        return 0;
    }
    if (rx->n < 4 || rx->n < rx->buf[2] + 4) return 0;

    /* ---- complete: check, deliver, reset */
    const int plen = rx->buf[2];
    const uint8_t crc = hil_link_crc8(&rx->buf[1], plen + 2);
    rx->n = 0;
    if (crc != rx->buf[3 + plen]) {
        ++rx->crc_errors;
        return -1;
    }
    *type = rx->buf[1];
    *len = plen;
    if (plen > 0) memcpy(payload, &rx->buf[3], (size_t)plen);
    ++rx->frames;
    return 1;
}

void hil_put_f32(uint8_t* p, float v)
{
    uint32_t u;
    memcpy(&u, &v, 4);
    p[0] = (uint8_t)u;
    p[1] = (uint8_t)(u >> 8);
    p[2] = (uint8_t)(u >> 16);
    p[3] = (uint8_t)(u >> 24);
}

float hil_get_f32(const uint8_t* p)
{
    uint32_t u = (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
    float v;
    memcpy(&v, &u, 4);
    return v;
}

int hil_telem_pack(const hil_telem_t* t, uint8_t* payload)
{
    if (!t || !payload) return -1;
    payload[0] = t->state;
    payload[1] = t->fault;
    hil_put_f32(&payload[2], t->q_rad);
    hil_put_f32(&payload[6], t->qd_rad_s);
    hil_put_f32(&payload[10], t->i_a);
    hil_put_f32(&payload[14], t->vbat_v);
    hil_put_f32(&payload[18], t->t_motor_c);
    hil_put_f32(&payload[22], t->t_winding_est_c);
    hil_put_f32(&payload[26], t->derate);
    return HIL_TELEM_LEN;
}

int hil_telem_unpack(const uint8_t* payload, int len, hil_telem_t* t)
{
    if (!payload || !t || len != HIL_TELEM_LEN) return -1;
    t->state = payload[0];
    t->fault = payload[1];
    t->q_rad = hil_get_f32(&payload[2]);
    t->qd_rad_s = hil_get_f32(&payload[6]);
    t->i_a = hil_get_f32(&payload[10]);
    t->vbat_v = hil_get_f32(&payload[14]);
    t->t_motor_c = hil_get_f32(&payload[18]);
    t->t_winding_est_c = hil_get_f32(&payload[22]);
    t->derate = hil_get_f32(&payload[26]);
    return 0;
}
