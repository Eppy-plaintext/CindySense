#pragma once

#include <math.h>
#include <stdint.h>
#include "batch_verification.h"

enum ExerciseMode : uint8_t { EX_PULLUP, EX_PUSHUP, EX_SQUAT };
inline const char *exerciseName(ExerciseMode mode) {
  return mode == EX_PULLUP ? "pullup" : mode == EX_PUSHUP ? "pushup" : "squat";
}

// Provisional values: replace after five labelled chest-mounted repetitions.
// Acceleration is m/s^2, displacement is metres, rates are degrees/second.
struct MotionParameters {
  float startQuietSeconds, triggerAccel, minDisplacement, returnDistance;
  float minDuration, maxDuration, cooldownSeconds, stableSeconds;
  float quietAccel, quietGyro, minPeakGyro, maxStartTiltDeg, maxCycleTiltDeg;
};
static const MotionParameters PULLUP_PARAMS = {4.0f, 1.25f, .12f, .07f, .8f, 5.0f, .7f, .25f, .50f, 25.0f, 0.0f, 35.0f, 55.0f};
static const MotionParameters PUSHUP_PARAMS = {4.0f, .85f, .07f, .06f, .7f, 5.0f, .7f, .25f, .40f, 30.0f, 0.0f, 15.0f, 30.0f};

class VerticalMotionDetector {
 public:
  VerticalMotionDetector() { reset(EX_PULLUP); }
  void reset(ExerciseMode mode) {
    mode_ = mode; params_ = mode == EX_PULLUP ? PULLUP_PARAMS : PUSHUP_PARAMS;
    count_ = 0; recalibrate();
  }
  void recalibrate() {
    calibrated_ = haveTime_ = haveGravity_ = false;
    quietStart_ = stableStart_ = 0; phase_ = 0; velocity_ = displacement_ = peak_ = peakGyro_ = 0;
    cooldownUntil_ = 0;
  }
  bool calibrated() const { return calibrated_; }
  uint32_t count() const { return count_; }
  float dynamicVertical() const { return dynamic_; }
  float displacement() const { return displacement_; }
  const MotionParameters &parameters() const { return params_; }
  bool process(const SensorSample &s) {
    const float a[3] = {s.ax, s.ay, s.az};
    const float gyro = sqrtf(s.gx*s.gx+s.gy*s.gy+s.gz*s.gz);
    uint32_t dtMs = haveTime_ ? s.timestamp - lastTime_ : 20;
    lastTime_ = s.timestamp; haveTime_ = true;
    if (dtMs > 80 || dtMs == 0) { phase_ = 0; stableStart_ = 0; velocity_ = displacement_ = 0; if(dtMs > 1000) recalibrate(); return false; }
    const float dt = dtMs * .001f;
    if (!haveGravity_) { for(int i=0;i<3;i++) gravity_[i]=a[i]; haveGravity_=true; }
    // Gravity low-pass provides the world vertical axis; the remainder is dynamic acceleration.
    const float alpha = dt / (.55f + dt);
    for(int i=0;i<3;i++) gravity_[i] += alpha*(a[i]-gravity_[i]);
    float norm = sqrtf(gravity_[0]*gravity_[0]+gravity_[1]*gravity_[1]+gravity_[2]*gravity_[2]);
    if (norm < 5.0f || norm > 15.0f) { phase_=0; return false; }
    dynamic_ = ((a[0]-gravity_[0])*gravity_[0]+(a[1]-gravity_[1])*gravity_[1]+(a[2]-gravity_[2])*gravity_[2])/norm;
    bool quiet = fabsf(dynamic_) < params_.quietAccel && gyro < params_.quietGyro;
    if (!calibrated_) {
      if (quiet) { if (!quietStart_) quietStart_=s.timestamp; if (s.timestamp-quietStart_ >= (uint32_t)(params_.startQuietSeconds*1000)) {
        calibrated_=true;for(int i=0;i<3;i++) baseline_[i]=gravity_[i]/norm;
      } }
      else quietStart_=0;
      return false;
    }
    if ((int32_t)(s.timestamp-cooldownUntil_) < 0) return false;
    float dot=0;for(int i=0;i<3;i++)dot+=baseline_[i]*gravity_[i]/norm;
    dot=fmaxf(-1.0f,fminf(1.0f,dot));
    float tilt=acosf(dot)*180.0f/3.14159265f;
    if(phase_ && tilt>params_.maxCycleTiltDeg){phase_=0;return false;}
    // Positive acceleration starts a pull-up; negative starts a push-up descent.
    const float direction = mode_ == EX_PULLUP ? 1.0f : -1.0f;
    if (phase_ == 0) {
      if (tilt<=params_.maxStartTiltDeg && direction*dynamic_ > params_.triggerAccel) {
        phase_=1; start_=s.timestamp; velocity_=displacement_=peak_=peakGyro_=0; stableStart_=0;
      } else return false;
    }
    if (s.timestamp-start_ > (uint32_t)(params_.maxDuration*1000)) { phase_=0; return false; }
    // Leaky integration bounds drift over a single movement. Physical displacement is approximate.
    velocity_ = .985f*velocity_ + direction*dynamic_*dt;
    displacement_ += velocity_*dt;
    if (displacement_ > peak_) peak_=displacement_;
    if (gyro > peakGyro_) peakGyro_=gyro;
    if (phase_ == 1 && peak_ >= params_.minDisplacement && velocity_ < -.02f) phase_=2;
    if (phase_ == 2 && fabsf(displacement_) <= params_.returnDistance &&
        s.timestamp-start_ >= (uint32_t)(params_.minDuration*1000) && peakGyro_ >= params_.minPeakGyro) {
      if (quiet) { if (!stableStart_) stableStart_=s.timestamp; }
      else stableStart_=0;
      if (stableStart_ && s.timestamp-stableStart_ >= (uint32_t)(params_.stableSeconds*1000)) {
        phase_=0; cooldownUntil_=s.timestamp+(uint32_t)(params_.cooldownSeconds*1000);
        ++count_; return true;
      }
    }
    return false;
  }
 private:
  ExerciseMode mode_; MotionParameters params_; uint32_t count_, lastTime_, quietStart_, stableStart_, start_, cooldownUntil_;
  uint8_t phase_; bool calibrated_, haveTime_, haveGravity_;
  float gravity_[3], baseline_[3], dynamic_, velocity_, displacement_, peak_, peakGyro_;
};
