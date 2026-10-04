/* SIL host stand-in for API/imu_update.h: the attitude the firmware publishes, in degrees
 * (TASK/StabilizerTask.c:870-877 and :1715 read it as degrees). */
#ifndef SIL_STUB_IMU_UPDATE_H
#define SIL_STUB_IMU_UPDATE_H

typedef struct {
    float pit;
    float rol;
    float yaw;
} _imu_st;

extern _imu_st imu_data;

#endif
