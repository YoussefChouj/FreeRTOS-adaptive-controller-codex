#include "platform_registry.h"
#include "subscribe.h"
#include "global_declare.h"

extern volatile uint32_t build_id[4];

static uint32_t s_reset_cause;

static void PutU16Le(uint8_t* out, uint16_t* idx, uint16_t value)
{
    out[(*idx)++] = (uint8_t)(value & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 8) & 0xFFU);
}

static void PutU32Le(uint8_t* out, uint16_t* idx, uint32_t value)
{
    out[(*idx)++] = (uint8_t)(value & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 8) & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 16) & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 24) & 0xFFU);
}

static uint8_t XorCrc8(const uint8_t* data, uint16_t len)
{
    uint8_t crc = 0U;
    uint16_t i;
    for (i = 0U; i < len; i++) {
        crc ^= data[i];
    }
    return crc;
}

static uint8_t BeginFrame(uint8_t type, uint8_t byte5, uint16_t payload_len,
                          uint8_t* out, uint16_t out_cap, uint16_t* idx)
{
    if ((out == 0) || (idx == 0) || (out_cap < (uint16_t)(7U + payload_len))) {
        return 0U;
    }
    out[0] = 0xAAU;
    out[1] = 0xBBU;
    out[2] = type;
    out[3] = (uint8_t)((payload_len >> 8) & 0xFFU);
    out[4] = (uint8_t)(payload_len & 0xFFU);
    out[5] = byte5;
    *idx = 6U;
    return 1U;
}

static void FinishFrame(uint8_t* out, uint16_t* idx, uint16_t* out_len)
{
    out[*idx] = XorCrc8(&out[2], (uint16_t)(*idx - 2U));
    *idx = (uint16_t)(*idx + 1U);
    *out_len = *idx;
}

void PlatformRegistry_Init(void)
{
    s_reset_cause = RCC->CSR;
    RCC->CSR |= RCC_CSR_RMVF;
}

uint8_t PlatformRegistry_IsDiscoveryCommand(uint8_t command)
{
    return ((command == PLATFORM_DISCOVERY_CMD) ||
            (command == PLATFORM_REGISTRY_DIGEST_CMD)) ? 1U : 0U;
}

uint8_t PlatformRegistry_BuildDiscovery(uint8_t* out, uint16_t out_cap, uint16_t* out_len)
{
    uint16_t idx;
    uint8_t i;
    uint32_t caps = PLATFORM_CAP_WIFI_COMMAND | PLATFORM_CAP_WIFI_SUBSCRIBE |
                    PLATFORM_CAP_MULTI_SLOT_STREAM | PLATFORM_CAP_STATIC_REGISTRY |
                    PLATFORM_CAP_BUILD_IDENTITY | PLATFORM_CAP_SWD_PROBE |
                    PLATFORM_CAP_LEGACY_TELEMETRY | PLATFORM_CAP_TYPED_DISCOVERY;
    const uint32_t* uid = (const uint32_t*)0x1FFF7A10U;
    const uint16_t payload_len = 62U;
    if (BeginFrame(PLATFORM_DISCOVERY_FRAME, PLATFORM_DISCOVERY_FORMAT_VERSION,
                   payload_len, out, out_cap, &idx) == 0U) {
        return 0U;
    }
    out[idx++] = 'U'; out[idx++] = 'A'; out[idx++] = 'V'; out[idx++] = 'R';
    out[idx++] = GS_PROTO_VERSION;
    out[idx++] = PLATFORM_DISCOVERY_FORMAT_VERSION;
    PutU16Le(out, &idx, PLATFORM_SCHEMA_VERSION);
    PutU16Le(out, &idx, PLATFORM_REGISTRY_VERSION);
    PutU16Le(out, &idx, PLATFORM_HARDWARE_ID);
    PutU32Le(out, &idx, caps);
    PutU32Le(out, &idx, PLATFORM_REGISTRY_CRC32);
    for (i = 0U; i < 4U; i++) PutU32Le(out, &idx, build_id[i]);
    for (i = 0U; i < 3U; i++) PutU32Le(out, &idx, uid[i]);
    PutU32Le(out, &idx, s_reset_cause);
    PutU16Le(out, &idx, SUBSCRIBE_MAX_STREAM_RANGES);
    PutU16Le(out, &idx, SUBSCRIBE_MAX_SLOTS);
    PutU16Le(out, &idx, SUBSCRIBE_STREAM_MAX_BYTES);
    PutU16Le(out, &idx, SUBSCRIBE_SEND_TASK_HZ);
    PutU16Le(out, &idx, PLATFORM_DESCRIPTOR_COUNT);
    FinishFrame(out, &idx, out_len);
    return 1U;
}

uint8_t PlatformRegistry_BuildDigest(uint8_t* out, uint16_t out_cap, uint16_t* out_len)
{
    uint16_t idx;
    uint8_t i;
    const uint16_t payload_len = (uint16_t)(12U + PLATFORM_KIND_COUNT * 4U);
    if (BeginFrame(PLATFORM_REGISTRY_DIGEST_FRAME, PLATFORM_KIND_COUNT,
                   payload_len, out, out_cap, &idx) == 0U) {
        return 0U;
    }
    out[idx++] = 'R'; out[idx++] = 'E'; out[idx++] = 'G'; out[idx++] = '1';
    PutU16Le(out, &idx, PLATFORM_REGISTRY_VERSION);
    PutU16Le(out, &idx, PLATFORM_SCHEMA_VERSION);
    PutU32Le(out, &idx, PLATFORM_REGISTRY_CRC32);
    for (i = 0U; i < PLATFORM_KIND_COUNT; i++) {
        out[idx++] = g_platform_kind_summaries[i].kind;
        out[idx++] = 0U;
        PutU16Le(out, &idx, g_platform_kind_summaries[i].count);
    }
    FinishFrame(out, &idx, out_len);
    return 1U;
}
