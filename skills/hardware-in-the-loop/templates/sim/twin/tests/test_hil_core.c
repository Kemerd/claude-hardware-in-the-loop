/**
 * @file test_hil_core.c
 * @brief Host suite of the firmware core alone, against a scripted HAL (no twin): the codec, the conversions,
 *        the state machine and the fault reactions.
 *
 * The scripted HAL returns exactly the raw values each check needs, so every assertion has one cause.
 * Prints exactly one "[hil] hil_core: N/N checks passed, 0 failed" line.
 */
#define HIL_CHECK_IMPLEMENTATION
#include "hil_check.h"

#include <math.h>
#include <string.h>

#include "hil_core/ctrl.h"
#include "hil_core/link.h"

/* ------------------------------------------------------------------------------------------------------------------
 *  Scripted HAL
 * ------------------------------------------------------------------------------------------------------------------ */
typedef struct {
    uint64_t t_us;
    int32_t enc;
    int enc_fail;
    uint32_t adc[HIL_ADC_COUNT];
    float last_cmd;
    int writes;
    uint8_t rx[256];
    int rx_n;
    uint8_t tx[1024];
    int tx_n;
} script;

static uint64_t s_time(void* c) { return ((script*)c)->t_us; }
static int s_enc(void* c, int32_t* v)
{
    script* s = (script*)c;
    if (s->enc_fail) return -1;
    *v = s->enc;
    return 0;
}
static int s_adc(void* c, int ch, uint32_t* v)
{
    if (ch < 0 || ch >= HIL_ADC_COUNT) return -1;
    *v = ((script*)c)->adc[ch];
    return 0;
}
static int s_motor(void* c, float cmd)
{
    script* s = (script*)c;
    s->last_cmd = cmd;
    ++s->writes;
    return 0;
}
static int s_recv(void* c, uint8_t* b, int cap)
{
    script* s = (script*)c;
    int n = s->rx_n < cap ? s->rx_n : cap;
    memcpy(b, s->rx, (size_t)n);
    memmove(s->rx, s->rx + n, (size_t)(s->rx_n - n));
    s->rx_n -= n;
    return n;
}
static int s_send(void* c, const uint8_t* b, int len)
{
    script* s = (script*)c;
    if (s->tx_n + len <= (int)sizeof s->tx) {
        memcpy(s->tx + s->tx_n, b, (size_t)len);
        s->tx_n += len;
    }
    return 0;
}

/** @brief Queue one frame for the firmware. */
static void send(script* s, uint8_t type, const uint8_t* pl, int len)
{
    s->rx_n += hil_link_encode(type, pl, len, s->rx + s->rx_n, (int)sizeof s->rx - s->rx_n);
}

/** @brief A board matching the example rig. */
static hil_core_board_t board(void)
{
    hil_core_board_t b;
    memset(&b, 0, sizeof b);
    b.drive = HIL_DRIVE_DC_DUTY;
    b.encoder_cpr = 48;
    b.gear_ratio = 50;
    b.gear_efficiency = 0.7f;
    b.kt_nm_per_a = 0.02f;
    b.r_ohm = 2.0f;
    b.adc_bits = 12;
    b.adc_vref_v = 3.3f;
    b.vbat_divider = 5.0f;
    b.isense_v_per_a = 0.2f;
    b.isense_offset_v = 1.65f;
    b.ntc_r25_ohm = 10000;
    b.ntc_beta_k = 3950;
    b.ntc_r_top_ohm = 10000;
    b.gravity_nm = 0.44f;
    b.i_max_a = 4.0f;
    b.q_home_rad = -1.75f;
    b.q_soft_min_rad = -1.6f;
    b.q_soft_max_rad = 1.6f;
    return b;
}

/** @brief Pin voltage -> 12-bit code. */
static uint32_t code(float v) { return (uint32_t)lroundf(v / 3.3f * 4095.0f); }

int main(void)
{
    /* ---- codec: round trip, CRC catches a flipped bit, resync after garbage */
    uint8_t pl[4], frame[HIL_LINK_MAX_FRAME], out[HIL_LINK_MAX_PAYLOAD], type = 0;
    hil_put_f32(pl, 1.25f);
    int n = hil_link_encode(HIL_MSG_SET_POS, pl, 4, frame, (int)sizeof frame);
    HIL_CHECK_EQ_INT(n, 8);
    hil_link_rx_t rx;
    memset(&rx, 0, sizeof rx);
    int len = 0, got = 0;
    hil_link_feed(&rx, 0x13, &type, out, &len); /* garbage before the frame */
    for (int i = 0; i < n; ++i) got = hil_link_feed(&rx, frame[i], &type, out, &len);
    HIL_CHECK_EQ_INT(got, 1);
    HIL_CHECK_EQ_INT(type, HIL_MSG_SET_POS);
    HIL_CHECK_NEAR(hil_get_f32(out), 1.25, 0.0);
    frame[4] ^= 0x01;
    for (int i = 0; i < n; ++i) got = hil_link_feed(&rx, frame[i], &type, out, &len);
    HIL_CHECK_EQ_INT(got, -1);
    HIL_CHECK_EQ_INT((int)rx.crc_errors, 1);
    HIL_CHECK_EQ_INT(hil_link_encode(1, pl, HIL_LINK_MAX_PAYLOAD + 1, frame, (int)sizeof frame), -1);

    /* ---- init refuses a HAL with a hole and a non-physical board */
    script s;
    memset(&s, 0, sizeof s);
    hal_t hal = {&s, s_time, s_enc, s_adc, s_motor, s_recv, s_send};
    hil_core_board_t b = board();
    hil_core_tuning_t t = hil_core_default_tuning();
    hil_core_t c;
    hal_t holed = hal;
    holed.adc_read = NULL;
    HIL_CHECK_EQ_INT(hil_core_init(&c, &holed, &b, &t), -1);
    hil_core_board_t bad = b;
    bad.encoder_cpr = 0;
    HIL_CHECK_EQ_INT(hil_core_init(&c, &hal, &bad, &t), -1);
    HIL_CHECK_EQ_INT(hil_core_init(&c, &hal, &b, &t), 0);

    /* ---- conversions: 12.0 V battery, 0 A, 25 degC (NTC at mid-rail) */
    s.adc[HIL_ADC_VBAT] = code(12.0f / 5.0f);
    s.adc[HIL_ADC_ISENSE] = code(1.65f);
    s.adc[HIL_ADC_NTC] = code(1.65f);
    HIL_CHECK_EQ_INT(hil_core_step(&c), 0);
    HIL_CHECK_NEAR(c.vbat, 12.0, 0.01);
    HIL_CHECK_NEAR(c.i_meas, 0.0, 0.01);
    HIL_CHECK_NEAR(c.t_motor_c, 25.0, 0.1);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_IDLE);
    HIL_CHECK_NEAR(s.last_cmd, 0.0, 0.0);
    HIL_CHECK(s.tx_n > 0); /* telemetry on tick 0 */

    /* ---- encoder wrap: from INT32_MAX to INT32_MIN is +1 count (about 1 rad/s after the filter), not -4e9 */
    hil_core_t cw;
    s.enc = 2147483647;
    HIL_CHECK_EQ_INT(hil_core_init(&cw, &hal, &b, &t), 0);
    hil_core_step(&cw);
    s.enc = (int32_t)(-2147483647 - 1);
    hil_core_step(&cw);
    HIL_CHECK(cw.qd > 0.0f && cw.qd < 5.0f);
    s.enc = 0;
    s.tx_n = 0;

    /* ---- ARM -> HOMING drives towards the stop (negative duty for home_speed < 0) */
    uint8_t one = 1, zero = 0;
    send(&s, HIL_MSG_ARM, &one, 1);
    for (int i = 0; i < 50; ++i) hil_core_step(&c);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_HOMING);
    HIL_CHECK(s.last_cmd < 0.0f);

    /* ---- encoder stops answering: FAULT, motor command 0 on the next tick */
    s.enc_fail = 1;
    hil_core_step(&c);
    hil_core_step(&c);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_FAULT);
    HIL_CHECK(c.fault & HIL_FAULT_ENCODER);
    HIL_CHECK_NEAR(s.last_cmd, 0.0, 0.0);

    /* ---- ARM is refused in FAULT; DISARM clears it */
    s.enc_fail = 0;
    send(&s, HIL_MSG_ARM, &one, 1);
    hil_core_step(&c);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_FAULT);
    send(&s, HIL_MSG_ARM, &zero, 1);
    hil_core_step(&c);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_IDLE);
    HIL_CHECK_EQ_INT(c.fault, 0);

    /* ---- an open NTC (pin at the rail) is an ADC fault while driving */
    send(&s, HIL_MSG_ARM, &one, 1);
    hil_core_step(&c);
    s.adc[HIL_ADC_NTC] = 4095;
    hil_core_step(&c);
    HIL_CHECK(c.fault & HIL_FAULT_ADC);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_FAULT);

    /* ---- derate: 80 degC is half way through the 70..90 band -> 1 - 0.5 * 0.7 = 0.65 */
    /* ---- while the wire is still open, a DISARM clears the bits but the next read latches them again, and ARM is
     *      refused with a latched fault */
    send(&s, HIL_MSG_ARM, &zero, 1);
    hil_core_step(&c);
    HIL_CHECK(c.fault & HIL_FAULT_ADC);
    send(&s, HIL_MSG_ARM, &one, 1);
    hil_core_step(&c);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_IDLE);
    /* NTC repaired at 80 degC: R = 10k exp(3950 (1/353.15 - 1/298.15)) */
    const float r80 = 10000.0f * expf(3950.0f * (1.0f / 353.15f - 1.0f / 298.15f));
    s.adc[HIL_ADC_NTC] = code(3.3f * r80 / (r80 + 10000.0f));
    send(&s, HIL_MSG_ARM, &zero, 1);
    hil_core_step(&c);
    HIL_CHECK_EQ_INT(c.fault, 0);
    HIL_CHECK_NEAR(c.t_motor_c, 80.0, 0.5);
    HIL_CHECK_NEAR(c.derate, 0.65, 0.02);

    /* ---- low battery while driving faults */
    s.adc[HIL_ADC_NTC] = code(1.65f);
    send(&s, HIL_MSG_ARM, &one, 1);
    hil_core_step(&c);
    HIL_CHECK_EQ_INT(c.state, HIL_ST_HOMING);
    s.adc[HIL_ADC_VBAT] = code(9.0f / 5.0f);
    hil_core_step(&c);
    HIL_CHECK(c.fault & HIL_FAULT_LOW_BATT);

    /* ---- one motor write per tick, always */
    const int w0 = s.writes;
    for (int i = 0; i < 10; ++i) hil_core_step(&c);
    HIL_CHECK_EQ_INT(s.writes - w0, 10);
    return hil_check_summary("hil_core");
}
