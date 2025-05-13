import numpy as np
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment

class Track:
    def __init__(self, bbox, track_id):
        # bbox: [x1, y1, x2, y2, score]
        self.kf = KalmanFilter(dim_x=7, dim_z=4)
        # Initialize state transition and measurement matrices here as needed...
        # For simplicity, assume identity for now (you can fill with proper SORT params)
        self.kf.F = np.eye(7)
        self.kf.H = np.zeros((4, 7))
        self.kf.H[0, 0] = 1
        self.kf.H[1, 1] = 1
        self.kf.H[2, 2] = 1
        self.kf.H[3, 3] = 1
        # init state vector (7x1): [x, y, w, h, vx, vy, vw]
        init_state = np.array([bbox[0], bbox[1], bbox[2], bbox[3], 0, 0, 0], dtype=float)
        self.kf.x[:, 0] = init_state
        # process & measurement noise covariances (tweak as needed)
        self.kf.P *= 10.0
        self.kf.R *= 1.0
        self.id = track_id
        self.hits = 1
        self.no_losses = 0

    def predict(self):
        self.kf.predict()
        return self.kf.x

    def update(self, bbox):
        # measurement z: [x, y, w, h]
        z = np.array([bbox[0], bbox[1], bbox[2], bbox[3]], dtype=float)
        self.kf.update(z)
        self.hits += 1
        self.no_losses = 0

class Sort:
    def __init__(self, max_age=5, min_hits=3, iou_threshold=0.3):
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.tracks = []
        self.next_id = 1

    @staticmethod
    def iou(bb_det, bb_trk):
        # bb_det and bb_trk: [x1,y1,x2,y2]
        xi1 = max(bb_det[0], bb_trk[0])
        yi1 = max(bb_det[1], bb_trk[1])
        xi2 = min(bb_det[2], bb_trk[2])
        yi2 = min(bb_det[3], bb_trk[3])
        inter = max(0, xi2 - xi1) * max(0, yi2 - yi1)
        area1 = (bb_det[2] - bb_det[0]) * (bb_det[3] - bb_det[1])
        area2 = (bb_trk[2] - bb_trk[0]) * (bb_trk[3] - bb_trk[1])
        return inter / (area1 + area2 - inter + 1e-6)

    def update(self, dets):
        """
        dets: ndarray of shape (N,5): [x1,y1,x2,y2,score]
        returns: ndarray of shape (M,5): [x1,y1,x2,y2,track_id]
        """
        # 1) Predict all tracks
        for t in self.tracks:
            t.predict()

        # 2) Associate
        N = dets.shape[0]
        M = len(self.tracks)
        if M > 0 and N > 0:
            # build IOU cost matrix
            iou_mat = np.zeros((N, M), dtype=float)
            for i in range(N):
                for j, t in enumerate(self.tracks):
                    det_box = dets[i, :4]
                    trk_box = self.tracks[j].kf.x[:4, 0]
                    iou_mat[i, j] = 1.0 - self.iou(det_box, trk_box)
            row, col = linear_sum_assignment(iou_mat)
            matched, unmatched_dets, unmatched_trks = [], [], []
            for i in range(N):
                if i not in row:
                    unmatched_dets.append(i)
            for j in range(M):
                if j not in col:
                    unmatched_trks.append(j)
            for r, c in zip(row, col):
                if iou_mat[r, c] <= (1.0 - self.iou_threshold):
                    matched.append((r, c))
                else:
                    unmatched_dets.append(r)
                    unmatched_trks.append(c)
        else:
            matched = []
            unmatched_dets = list(range(N))
            unmatched_trks = list(range(M))

        # 3) Update matched tracks
        for det_idx, trk_idx in matched:
            self.tracks[trk_idx].update(dets[det_idx])

        # 4) Create new tracks for unmatched detections
        for idx in unmatched_dets:
            self.tracks.append(Track(dets[idx], self.next_id))
            self.next_id += 1

        # 5) Age out lost tracks and collect results
        results = []
        for t in self.tracks[:]:
            if t.no_losses > self.max_age:
                self.tracks.remove(t)
            elif t.hits >= self.min_hits:
                x1, y1, x2, y2 = t.kf.x[:4, 0]
                results.append([float(x1), float(y1), float(x2), float(y2), t.id])
            t.no_losses += 1

        if results:
            return np.array(results, dtype=float)
        else:
            return np.empty((0, 5), dtype=float)
