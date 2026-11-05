#pragma once

// Fixed-state, sample-at-a-time phase-1 port of the causal Python detector.
// Calibration uses a mean instead of median and omits diagnostics/uncertainty scoring.
#include <math.h>
#include <stdint.h>

#ifndef PI
#define PI 3.14159265358979323846
#endif

struct SquatEvent {
  uint32_t eventSequence;
  uint32_t repetition;
  uint32_t timestampMs;
};

class SquatDetector {
 public:
  void reset() {
    count_ = 0; lastTime_ = firstTime_ = 0; haveTime_ = false;
    referenceReady_ = false; calibrationFailed_ = false; orientation_ = 0; rawAngle_ = lastRelative_ = 0;
    haveRawAngle_=haveRelative_=false;filtered_ = false; quietCount_ = quietAt_ = 0; quietSum_ = 0;
    cooldownUntil_ = 0; sequence_ = 0;
    for (int i = 0; i < 2; ++i) clearCycle(cycles_[i]);
  }

  void recalibrate() {
    haveTime_ = false; referenceReady_ = false; calibrationFailed_ = false; orientation_ = 0;
    filtered_ = false; quietCount_ = quietAt_ = 0; quietSum_ = 0;
    rawAngle_ = lastRelative_ = 0;haveRawAngle_=haveRelative_=false;cooldownUntil_=0;
    for (int i = 0; i < 2; ++i) clearCycle(cycles_[i]);
  }

  bool process(const SensorSample &s, SquatEvent &event) {
    const float t = s.timestamp * 0.001f;
    const float acc[3] = {s.ax, s.ay, s.az};
    const float gyro[3] = {s.gx, s.gy, s.gz};
    if (haveTime_ && t < lastTime_ - 1.0f) recalibrate();
    float dt = haveTime_ ? t - lastTime_ : 0.02f;
    if (dt < 0) return false;
    if (dt > 0.040001f) {
      clearCycle(cycles_[0]); clearCycle(cycles_[1]);
      filtered_ = false; lastRelative_ = 0;haveRelative_=haveRawAngle_=false;
      if (dt >= 1.0f) { referenceReady_ = false; calibrationFailed_=false; orientation_ = 0; quietCount_ = quietAt_ = 0; quietSum_ = 0; firstTime_=t; }
    }
    if (!haveTime_) firstTime_ = t;
    lastTime_ = t; haveTime_ = true;
    float alpha = 1.0f - expf(-2.0f * PI * 1.2f * fminf(dt, 0.04f));
    if (!filtered_) { for (int i=0;i<3;i++) { fa_[i]=acc[i]; fg_[i]=gyro[i]; } filtered_=true; }
    else for (int i=0;i<3;i++) { fa_[i]+=alpha*(acc[i]-fa_[i]); fg_[i]+=alpha*(gyro[i]-fg_[i]); }
    float angle = atan2f(fa_[0], -fa_[1]) * 180.0f / PI;
    if(haveRawAngle_)angle += 360.0f * roundf((rawAngle_ - angle) / 360.0f);
    rawAngle_ = angle;
    haveRawAngle_=true;
    float gn = sqrtf(fg_[0]*fg_[0]+fg_[1]*fg_[1]+fg_[2]*fg_[2]);
    if (!referenceReady_) {
      if(calibrationFailed_)return false;
      if (gn < 8.0f) {
        if (quietCount_ < 400) { quiet_[quietAt_] = angle; quietSum_ += angle; quietCount_++; }
        else { quietSum_ += angle - quiet_[quietAt_]; quiet_[quietAt_] = angle; }
        quietAt_=(quietAt_+1)%400;
      }
      if (t-firstTime_ >= 1.0f && quietCount_ >= 20) { reference_=quietSum_/quietCount_; referenceReady_=true; }
      else if (t-firstTime_ >= 8.0f) { // match Python's fail-closed uncertain calibration
        calibrationFailed_=true;
      }
      return false;
    }
    float relative=angle-reference_;
    float rate=haveRelative_ ? (relative-lastRelative_)/fmaxf(dt,0.001f) : 0;
    lastRelative_=relative;haveRelative_=true;
    if (cycles_[0].phase==0 && cycles_[1].phase==0 && gn<8.0f)
      reference_ += (1-expf(-2.0f*PI*0.02f*dt))*(angle-reference_);
    int signs[2]={orientation_ ? orientation_ : 1, orientation_ ? orientation_ : -1};
    for(int i=0;i<(orientation_?1:2);i++) {
      int sign=signs[i]; Cycle &c=cycles_[sign>0?0:1];
      if (step(c, sign, t, sign*relative, sign*angle, sign*rate, fg_)) {
        if (!orientation_) { orientation_=sign; clearCycle(cycles_[sign>0?1:0]); }
        count_++; event.eventSequence=++sequence_; event.repetition=count_; event.timestampMs=s.timestamp; return true;
      }
    }
    return false;
  }
  uint32_t count() const { return count_; }
  bool calibrated() const { return referenceReady_; }

 private:
  struct Cycle { uint8_t phase; bool armed; float start,bottom,peak,peakRaw,peakTime; float descent[3][2],ascent[3][2],corr[3],energy[3],tiltEnergy; };
  void clearCycle(Cycle &c) { c.phase=0;c.armed=false;c.start=c.bottom=c.peak=c.peakRaw=c.peakTime=0;c.tiltEnergy=0;for(int a=0;a<3;a++){c.corr[a]=c.energy[a]=0;for(int d=0;d<2;d++)c.descent[a][d]=c.ascent[a][d]=0;} }
  bool step(Cycle &c,int sign,float t,float tilt,float raw,float rate,const float *g) {
    if(t<cooldownUntil_) return false;
    if(c.phase==0){ if(tilt<=4.5f)c.armed=true; if(c.armed&&tilt>=5.0f&&rate>0){c.phase=1;c.armed=false;c.start=c.peakTime=t;c.peak=tilt;c.peakRaw=raw;} return false; }
    if(t-c.start>6.0f){clearCycle(c);return false;}
    if((c.phase==1||c.phase==2)&&tilt>c.peak){c.peak=tilt;c.peakRaw=raw;c.peakTime=t;}
    float (*mx)[2]=c.phase==3?c.ascent:c.descent;
    if(fabsf(rate)>=3){c.tiltEnergy+=rate*rate;for(int a=0;a<3;a++){c.corr[a]+=rate*g[a];c.energy[a]+=g[a]*g[a];}}
    for(int a=0;a<3;a++){if(g[a]>mx[a][0])mx[a][0]=g[a];if(-g[a]>mx[a][1])mx[a][1]=-g[a];}
    if(c.phase==1){if(tilt<=4.5f&&c.peak<10){clearCycle(c);return false;}if(tilt>=10&&t-c.start>=.2f)c.phase=2;}
    else if(c.phase==2){if(tilt<=c.peak-3&&rate<0){c.phase=3;c.bottom=c.peakTime;}}
    else if(c.phase==3&&tilt<=4.5f){float ret=c.peakRaw-raw;if(t-c.bottom<.4f||t-c.start<.7f||t-c.start>6||ret<4){clearCycle(c);return false;}
      float best=-2,des=0,asc=0; for(int a=0;a<3;a++)for(int gs=-1;gs<=1;gs+=2){int dir=gs*sign>0?0:1;float d=c.descent[a][dir],u=c.ascent[a][1-dir],strength=fminf(d,u);float corr=(c.tiltEnergy&&c.energy[a])?c.corr[a]/sqrtf(c.tiltEnergy*c.energy[a]):0;corr*=gs*sign;if(strength>=5&&corr>best){best=corr;des=d;asc=u;}}
      if(best<0.5f){clearCycle(c);return false;}clearCycle(c);cooldownUntil_=t+.75f;return true;
    }
    return false;
  }
  uint32_t count_,sequence_; float lastTime_,firstTime_; bool haveTime_,referenceReady_,calibrationFailed_,filtered_,haveRawAngle_,haveRelative_; int orientation_; float reference_,rawAngle_,lastRelative_,cooldownUntil_;
  float fa_[3],fg_[3],quiet_[400],quietSum_; uint16_t quietCount_,quietAt_; Cycle cycles_[2];
};
