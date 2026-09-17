#ifndef PLATFORM_REGISTRY_H
#define PLATFORM_REGISTRY_H

#include "stm32f4xx.h"

#define PLATFORM_DISCOVERY_CMD              0x22U
#define PLATFORM_REGISTRY_DIGEST_CMD         0x23U
#define PLATFORM_DISCOVERY_FRAME             0x22U
#define PLATFORM_REGISTRY_DIGEST_FRAME       0x23U
#define PLATFORM_DISCOVERY_FORMAT_VERSION    1U

#define PLATFORM_CAP_WIFI_COMMAND        (1UL << 0)
#define PLATFORM_CAP_WIFI_SUBSCRIBE      (1UL << 1)
#define PLATFORM_CAP_MULTI_SLOT_STREAM   (1UL << 2)
#define PLATFORM_CAP_STATIC_REGISTRY     (1UL << 3)
#define PLATFORM_CAP_BUILD_IDENTITY      (1UL << 4)
#define PLATFORM_CAP_SWD_PROBE           (1UL << 5)
#define PLATFORM_CAP_LEGACY_TELEMETRY    (1UL << 6)
#define PLATFORM_CAP_TYPED_DISCOVERY     (1UL << 7)

typedef struct {
    uint16_t id;
    uint8_t kind;
    uint8_t type;
    int32_t scale_milli;
    int32_t scale_den_milli;
    int32_t min_milli;
    int32_t max_milli;
    uint16_t rate_hz;
    uint8_t permissions;
    uint8_t safety;
    uint32_t dependencies;
    uint8_t version;
    const char* name;
    const char* unit;
    const char* owner;
} PlatformDescriptor_t;

typedef struct {
    uint8_t kind;
    uint8_t reserved;
    uint16_t count;
} PlatformKindSummary_t;

void PlatformRegistry_Init(void);
uint8_t PlatformRegistry_IsDiscoveryCommand(uint8_t command);
uint8_t PlatformRegistry_BuildDiscovery(uint8_t* out, uint16_t out_cap, uint16_t* out_len);
uint8_t PlatformRegistry_BuildDigest(uint8_t* out, uint16_t out_cap, uint16_t* out_len);

#include "platform_registry_gen.h"

#endif
