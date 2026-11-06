// Build with a C++11 compiler; accepts the mixed Serial log produced by RECORD.
#include "../exercise_detector.h"
#include "../squat_detector.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

int main(int argc,char **argv) {
  if(argc!=3){fprintf(stderr,"usage: replay_chest_csv pullup|pushup|squat serial-log.txt\n");return 2;}
  ExerciseMode mode;
  if(!strcmp(argv[1],"pullup"))mode=EX_PULLUP;
  else if(!strcmp(argv[1],"pushup"))mode=EX_PUSHUP;
  else if(!strcmp(argv[1],"squat"))mode=EX_SQUAT;
  else {fprintf(stderr,"invalid exercise\n");return 2;}
  FILE *file=fopen(argv[2],"r");if(!file){perror(argv[2]);return 2;}
  VerticalMotionDetector motion;motion.reset(mode);SquatDetector squat;squat.reset();
  char line[256];uint32_t first=0,last=0,rows=0,gaps=0,events=0;
  float baselineNoise=0,motionPeak=0,peakDisplacement=0;
  while(fgets(line,sizeof(line),file)) {
    SensorSample s;unsigned long timestamp;
    if(sscanf(line,"%lu,%f,%f,%f,%f,%f,%f",&timestamp,&s.ax,&s.ay,&s.az,&s.gx,&s.gy,&s.gz)!=7)continue;
    s.timestamp=(uint32_t)timestamp;
    if(!rows) first=s.timestamp;
    if(rows && s.timestamp-last>40)gaps++;
    last=s.timestamp;rows++;
    bool found=false;
    if(mode==EX_SQUAT){SquatEvent e;found=squat.process(s,e);}
    else {
      found=motion.process(s);
      float signal=fabsf(motion.dynamicVertical());
      if(s.timestamp-first<4000){if(signal>baselineNoise)baselineNoise=signal;}
      else {if(signal>motionPeak)motionPeak=signal;if(fabsf(motion.displacement())>peakDisplacement)peakDisplacement=fabsf(motion.displacement());}
    }
    if(found){events++;printf("event %lu at %lu ms\n",(unsigned long)events,(unsigned long)s.timestamp);}
  }
  fclose(file);
  printf("samples=%lu duration=%.1f s gaps>40ms=%lu detected=%lu expected=5\n",
    (unsigned long)rows,rows?(last-first)/1000.0f:0.0f,(unsigned long)gaps,(unsigned long)events);
  if(mode!=EX_SQUAT)printf("quiet max |dynamic vertical|=%.2f m/s2, movement peak=%.2f m/s2, approximate displacement peak=%.3f m\n"
    "provisional trigger suggestion=max(quiet max*2, movement peak*0.35)=%.2f m/s2; inspect all five cycles before editing parameters\n",
    baselineNoise,motionPeak,peakDisplacement,fmaxf(baselineNoise*2,motionPeak*.35f));
  if(rows<200 || gaps)fprintf(stderr,"WARNING: recording is short or has gaps; replay may miss repetitions.\n");
  return rows?0:1;
}
