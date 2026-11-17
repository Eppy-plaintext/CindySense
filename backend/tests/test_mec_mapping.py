import unittest

from backend.mec_mapping import SIMULATED_SOURCE, MecWlanMappingError, map_wlan_access_points


class MecMappingTests(unittest.TestCase):
    def test_maps_required_and_optional_fields_without_unit_conversion(self):
        payload = [{'apId': {'bssid': 'aa:bb:cc:dd:ee:ff'},
                    'bssLoad': {'staCount': 1, 'channelUtilization': 37,
                                'availAdmCap': 120},
                    'apLocation': {'geolocation': {'datum': 1, 'lat': 43.7}}}]
        self.assertEqual(map_wlan_access_points(payload), [{
            'bssid': 'aa:bb:cc:dd:ee:ff', 'stationCount': 1,
            'channelUtilization': 37, 'availableAdmissionCapacity': 120,
            'location': {'geolocation': {'datum': 1, 'lat': 43.7}},
            'source': SIMULATED_SOURCE,
        }])

    def test_preserves_zero_values_from_actual_mec_schema(self):
        payload = [{'apId': {'bssid': '005C07070707'},
                    'apLocation': {'geolocation': {'datum': 1, 'lat': 361449150,
                                                   'long': 59149895}},
                    'bssLoad': {'availAdmCap': 0, 'channelUtilization': 0,
                                'staCount': 0}}]
        self.assertEqual(map_wlan_access_points(payload), [{
            'bssid': '005C07070707', 'stationCount': 0,
            'channelUtilization': 0, 'availableAdmissionCapacity': 0,
            'location': {'geolocation': {'datum': 1, 'lat': 361449150,
                                         'long': 59149895}},
            'source': SIMULATED_SOURCE,
        }])

    def test_empty_list(self):
        self.assertEqual(map_wlan_access_points([]), [])

    def test_missing_optional_fields(self):
        self.assertEqual(map_wlan_access_points([{'apId': {'bssid': 'a'},
                                                   'bssLoad': {'staCount': 0}}])[0], {
            'bssid': 'a', 'stationCount': 0, 'source': SIMULATED_SOURCE})

    def test_invalid_mandatory_fields(self):
        for item in ({}, {'apId': {}}, {'apId': {'bssid': ''}},
                     {'apId': {'bssid': 'a'}},
                     {'apId': {'bssid': 'a'}, 'bssLoad': {'staCount': -1}},
                     {'apId': {'bssid': 'a'}, 'bssLoad': {'staCount': True}}):
            with self.assertRaises(MecWlanMappingError):
                map_wlan_access_points([item])
