import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name('.env'))


def tinyiot_base_url() -> str:
    base = os.getenv('TINYIOT_BASE_URL', 'http://127.0.0.1:3001/TinyIoT').rstrip('/')
    return base[:-8] + '/TinyIoT' if base.lower().endswith('/tinyiot') else base


@dataclass(frozen=True)
class Settings:
    base_url: str = tinyiot_base_url()
    workout_origin: str = os.getenv('TINYIOT_WORKOUT_ORIGIN', '')
    discovery_origin: str = os.getenv('TINYIOT_DISCOVERY_ORIGIN', '')
    group_origin: str = os.getenv('TINYIOT_GROUP_ORIGIN', '')
    group_resource: str = os.getenv('TINYIOT_GROUP_RESOURCE', 'CindySense_AE/WorkoutMonitoring_GRP')
    cindypt_origin: str = os.getenv('TINYIOT_CINDYPT_ORIGIN', '')
    repetition_resource: str = os.getenv('TINYIOT_REPETITION_RESOURCE', 'CindySense_AE/RepetitionCount')
    workout_summary_resource: str = os.getenv('TINYIOT_WORKOUT_SUMMARY_RESOURCE', 'CindySense_AE/WorkoutSummary')
    workout_ts_resource: str = os.getenv('TINYIOT_WORKOUT_TS_RESOURCE', 'CindySense_AE/WorkoutPerformance_TS')
    workout_ts_read_origin: str = os.getenv('TINYIOT_WORKOUT_TS_READ_ORIGIN', 'CDashboard')
    target_plan_resource: str = os.getenv('TINYIOT_TARGET_PLAN_RESOURCE', 'CindyPT_AE/FitnessGoals')
    webcam_origin: str = os.getenv('TINYIOT_WEBCAM_ORIGIN', 'CWebcam')
    recommendations_resource: str = os.getenv('TINYIOT_RECOMMENDATIONS_RESOURCE', 'CindyPT_AE/AIRecommendations')
    training_plans_resource: str = os.getenv('TINYIOT_TRAINING_PLANS_RESOURCE', 'CindyPT_AE/TrainingPlans')
    workout_schedule_resource: str = os.getenv('TINYIOT_WORKOUT_SCHEDULE_RESOURCE', 'CindyPT_AE/WorkoutSchedule')
    recovery_resource: str = os.getenv('TINYIOT_RECOVERY_RESOURCE', 'CindyPT_AE/RecoveryRecommendations')
    simulation_writes_enabled: bool = os.getenv('CINDYSENSE_ENABLE_SIMULATION_WRITES', 'false').lower() == 'true'
    simulation_token: str = os.getenv('CINDYSENSE_SIMULATION_TOKEN', '')
    simulation_origin: str = os.getenv('TINYIOT_SIMULATION_ORIGIN', '')
    # Use a Windows host address reachable from WSL, never WSL's localhost.
    notification_url: str = os.getenv('CINDYSENSE_NOTIFICATION_URL', '')
    notification_token: str = os.getenv('CINDYSENSE_NOTIFICATION_TOKEN', '')
    subscription_origin: str = os.getenv('TINYIOT_SUBSCRIPTION_ORIGIN', '')
    notification_origin: str = os.getenv('TINYIOT_NOTIFICATION_ORIGIN', '')


settings = Settings()
