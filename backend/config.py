import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name('.env'))


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv('TINYIOT_BASE_URL', 'http://localhost:3000/TinyIoT').rstrip('/')
    workout_origin: str = os.getenv('TINYIOT_WORKOUT_ORIGIN', '')
    cindypt_origin: str = os.getenv('TINYIOT_CINDYPT_ORIGIN', '')
    repetition_resource: str = os.getenv('TINYIOT_REPETITION_RESOURCE', 'CindySense_AE/RepetitionCount')
    recommendations_resource: str = os.getenv('TINYIOT_RECOMMENDATIONS_RESOURCE', 'CindyPT_AE/AIRecommendations')
    simulation_writes_enabled: bool = os.getenv('CINDYSENSE_ENABLE_SIMULATION_WRITES', 'false').lower() == 'true'
    simulation_token: str = os.getenv('CINDYSENSE_SIMULATION_TOKEN', '')
    simulation_origin: str = os.getenv('TINYIOT_SIMULATION_ORIGIN', '')


settings = Settings()
