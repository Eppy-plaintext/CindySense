// Host replay smoke test: g++ -std=c++11 test_squat_detector.cpp -o test_squat_detector
#include "../batch_verification.h"
#include "../squat_detector.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>

static float angleAt(float t) {
  const float x[]={0,2,3,3.3f,4.2f,5,6,6.3f,7.2f,8,9,9.3f,10.2f,12};
  const float y[]={0,0,18,18,0,0,18,18,0,0,18,18,0,0};
  for(int i=0;i<13;i++)if(t>=x[i]&&t<=x[i+1])return y[i]+(y[i+1]-y[i])*(t-x[i])/(x[i+1]-x[i]);
  return 0;
}
int main(){
  for(int sign=-1;sign<=1;sign+=2){
    SquatDetector detector;detector.reset();int found=0;
    for(uint32_t i=0;i<600;i++){
      float t=i*.02f,a=sign*angleAt(t),prev=sign*angleAt(t>=.02f?t-.02f:0);
      float rad=a*PI/180.0f;
      SensorSample s={i*20,9.81f*sinf(rad),-9.81f*cosf(rad),0,0,0,-(a-prev)/.02f};
      SquatEvent e;if(detector.process(s,e)){found++;assert(e.eventSequence==(uint32_t)found);assert(e.repetition==(uint32_t)found);}
    }
    assert(found==3);
  }
  puts("squat detector synthetic replay passed");return 0;
}
