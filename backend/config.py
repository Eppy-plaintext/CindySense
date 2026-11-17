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
    discovery_ttl_seconds: float = float(os.getenv('TINYIOT_DISCOVERY_TTL_SECONDS', '30'))
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
    mec_enabled: bool = os.getenv('MEC_ENABLED', 'false').lower() == 'true'
    mec_wlan_api_base_url: str = os.getenv('MEC_WLAN_API_BASE_URL', '').strip()
    mec_request_timeout_seconds: float = float(os.getenv('MEC_REQUEST_TIMEOUT_SECONDS', '10'))
    mec_service_mgmt_api_base_url: str = os.getenv('MEC_SERVICE_MGMT_API_BASE_URL', '').strip()
    mec_service_discovery_ttl_seconds: float = float(os.getenv('MEC_SERVICE_DISCOVERY_TTL_SECONDS', '30'))
    mec_service_discovery_enabled: bool = os.getenv('MEC_SERVICE_DISCOVERY_ENABLED', 'false').lower() == 'true'
    mec_allow_configured_fallback: bool = os.getenv('MEC_ALLOW_CONFIGURED_FALLBACK', 'false').lower() == 'true'
    mec_bridge_enabled: bool = os.getenv('MEC_BRIDGE_ENABLED', 'false').lower() == 'true'
    mec_bridge_origin: str = os.getenv('MEC_BRIDGE_ORIGIN', '')
    mec_bridge_ae: str = os.getenv('MEC_BRIDGE_AE', 'MECBridge_AE')
    mec_bridge_wlan_container: str = os.getenv('MEC_BRIDGE_WLAN_CONTAINER', 'WLANInformation')
    pace_warning_slowdown_ratio: float = float(os.getenv('CINDYSENSE_PACE_WARNING_RATIO', '1.35'))
    pace_warning_minimum_intervals: int = int(os.getenv('CINDYSENSE_PACE_WARNING_MIN_INTERVALS', '7'))
    pace_warning_required_evidence: int = int(os.getenv('CINDYSENSE_PACE_WARNING_EVIDENCE', '2'))
    pace_warning_cooldown_seconds: int = int(os.getenv('CINDYSENSE_PACE_WARNING_COOLDOWN_SECONDS', '60'))
    subscription_monitor_enabled: bool = os.getenv('CINDYSENSE_SUBSCRIPTION_MONITOR_ENABLED', 'true').lower() == 'true'
    subscription_monitor_interval_seconds: float = float(os.getenv('CINDYSENSE_SUBSCRIPTION_MONITOR_INTERVAL_SECONDS', '60'))
    subscription_recovery_enabled: bool = os.getenv('CINDYSENSE_SUBSCRIPTION_RECOVERY_ENABLED', 'true').lower() == 'true'
    subscription_monitor_max_backoff_seconds: float = float(os.getenv('CINDYSENSE_SUBSCRIPTION_MONITOR_MAX_BACKOFF_SECONDS', '300'))


settings = Settings()
