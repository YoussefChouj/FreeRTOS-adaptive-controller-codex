#ifndef MY_TYPES_H
#define MY_TYPES_H

typedef struct {
    float FB; float Des; float Kp; float Ki; float Kd;
    float Up; float Ui; float Ud; float E; float PreE;
    float SumE; float U; float UMax; float UpMax; float UiMax;
    float UdMax; float SumEMax; float EMin;
    int aw_mode; float Kt;
} PIDTypeDef;

typedef struct {
    PIDTypeDef pitchPID, rollPID, yawPID;
    PIDTypeDef gyroxPID, gyroyPID, gyrozPID;
    PIDTypeDef Z_posPID, Z_ratePID;
    PIDTypeDef locxPID, locyPID, locxsPID, locysPID;
    PIDTypeDef stree_yaw_speed, stree_pitch_speed;
} CtrlerTypeDef;

#endif
