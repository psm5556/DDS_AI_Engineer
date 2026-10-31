"""시험이 이 PC의 실제 마켓 항목(~/.dds-agent/market)을 읽지 않게 한다. 실제 항목이 있으면 규칙 파일 바이트 비교가 달라진다."""
import os
import tempfile

os.environ.setdefault("DDS_MARKET_DIR", tempfile.mkdtemp(prefix="dds_market_test_"))
