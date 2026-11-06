#include "../exercise_detector.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>

static SensorSample sample(uint32_t ms, float vertical) {
  SensorSample s={ms,0,9.80665f+vertical,0,0,0,0};
  return s;
}

static int replay(ExerciseMode mode, bool move, bool gap) {
  VerticalMotionDetector detector;detector.reset(mode);
  int found=0;
  for(uint32_t i=0;i<1200;i++) {
    uint32_t ms=i*20;
    if(gap && i>=290 && i<300) continue;
    float vertical=0;
    if(move && i>=250 && i<850) {
      float local=(i-250)*.02f;
      float cycle=fmodf(local,4.0f);
      if(cycle<2.0f) {
        float direction=mode==EX_PULLUP?1.0f:-1.0f;
        vertical=direction*3.5f*cosf(3.14159265f*cycle);
      }
    }
    if(detector.process(sample(ms,vertical))) found++;
  }
  return found;
}

static int shake(ExerciseMode mode) {
  VerticalMotionDetector detector;detector.reset(mode);int found=0;
  for(uint32_t i=0;i<600;i++){
    float acceleration=i>250&&i<400 ? 3.5f*sinf((i-250)*.02f*50.0f) : 0;
    if(detector.process(sample(i*20,acceleration)))found++;
  }
  return found;
}

int main(){
  assert(replay(EX_PULLUP,false,false)==0);
  assert(replay(EX_PUSHUP,false,false)==0);
  assert(replay(EX_PULLUP,true,false)==3);
  assert(replay(EX_PUSHUP,true,false)==3);
  assert(replay(EX_PULLUP,true,true)==1);
  assert(replay(EX_PUSHUP,true,true)==1);
  assert(shake(EX_PULLUP)==0);
  assert(shake(EX_PUSHUP)==0);
  puts("synthetic motion, rest, and missing-frame tests passed");
  return 0;
}
