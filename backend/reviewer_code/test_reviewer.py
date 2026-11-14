import math
import unittest

from fastapi.testclient import TestClient
from backend.reviewer_code import server


class ReviewerTests(unittest.TestCase):
    def setUp(self):
        server.settings = server.Settings()
        self.places = server.places()
        self.lat = self.places[0]['coordinates']['latitude']
        self.lon = self.places[0]['coordinates']['longitude']

    def pose(self, metres, heading=0):
        return server.Pose(latitude=self.lat-math.degrees(metres/6371000), longitude=self.lon, heading=heading)

    def test_range_and_heading(self):
        self.assertEqual(server.visible_reviews(self.pose(5), self.places)[0]['review'], 'Really liked this bar')
        self.assertFalse(server.visible_reviews(self.pose(10.01), self.places))
        self.assertFalse(server.visible_reviews(self.pose(5, 180), self.places))
        self.assertTrue(server.visible_reviews(self.pose(5, 359), self.places))
        self.assertTrue(server.visible_reviews(self.pose(5, 30), self.places))
        self.assertFalse(server.visible_reviews(self.pose(5, 30.01), self.places))
        self.assertFalse(server.visible_reviews(self.pose(0), self.places))

    def test_api_validation_and_simulation(self):
        with TestClient(server.app) as client:
            self.assertEqual(client.post('/api/reviews', json={'latitude':91,'longitude':0,'heading':0}).status_code, 422)
            self.assertEqual(client.post('/api/reviews', json={}).status_code, 422)
            client.post('/api/settings', json={'simulated':True})
            result = client.post('/api/reviews', json={'latitude':0,'longitude':0,'heading':180}).json()
            self.assertTrue(result['simulated'])
            self.assertEqual(result['reviews'][0]['rating'], 5)

    def test_calibration(self):
        with TestClient(server.app) as client:
            client.post('/api/settings', json={'north_offset':180})
            result = client.post('/api/reviews', json=self.pose(5,180).model_dump()).json()
            self.assertEqual(len(result['reviews']), 1)


if __name__ == '__main__':
    unittest.main()
