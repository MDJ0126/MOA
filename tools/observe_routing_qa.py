"""Observation-only QA server for isolated live worker; never starts another worker."""
from pathlib import Path
from unittest.mock import patch
from app import serve,Worker
from leader_inbox import LeaderInbox
from peer_scores import PeerScores
from slack_auto import SlackAuto
root=Path(__file__).resolve().parents[1]/'reports/routing-progress-20261025/data'
with patch.object(Worker,'recover',lambda self:None),patch.object(Worker,'loop',lambda self:self.stop.wait()),patch.object(LeaderInbox,'recover',lambda self:None),patch.object(PeerScores,'loop',lambda self:self.stop.wait()),patch.object(SlackAuto,'request_profiles',lambda self:None):
 serve(str(root),8768,'','http://127.0.0.1:8766')
