"""
Sector analysis for NSE (Nairobi Securities Exchange) stocks.

Groups stocks by sector and computes sector-level aggregates:
average return, average RSI, bullish/bearish ratio, average volume change.
"""

from logger import get_logger

logger = get_logger(__name__)

# NSE stock-to-sector mapping
NSE_SECTORS = {
    'SCOM': 'Telecommunication',
    'EQTY': 'Banking',
    'KCB': 'Banking',
    'COOP': 'Banking',
    'ABSA': 'Banking',
    'NCBA': 'Banking',
    'SCBK': 'Banking',
    'IMH': 'Banking',
    'DTB': 'Banking',
    'SBIC': 'Banking',
    'EABL': 'Manufacturing',
    'BAT': 'Manufacturing',
    'KPLC': 'Energy',
    'KEGN': 'Energy',
    'UMME': 'Energy',
    'KNRE': 'Insurance',
    'JUB': 'Insurance',
    'BRIT': 'Insurance',
    'CTUM': 'Investment',
    'HOME': 'Investment',
    'KQ': 'Commercial',
    'NMG': 'Commercial',
    'SASN': 'Agriculture',
    'KUKZ': 'Agriculture',
    'WTK': 'Agriculture',
}


class SectorAnalyzer:
    """Analyzes stocks grouped by sector."""

    def __init__(self, sector_map=None):
        self.sector_map = sector_map or NSE_SECTORS

    def get_sector(self, symbol):
        """Get the sector for a symbol."""
        return self.sector_map.get(symbol, 'Other')

    def analyze_sectors(self, stock_data, analysis_results):
        """
        Compute sector-level aggregates.

        Args:
            stock_data: dict of {symbol: DataFrame} (raw OHLCV data).
            analysis_results: dict of {symbol: analysis_result} from AnalysisEngine.

        Returns:
            dict: {sector_name: {metrics}}.
        """
        sectors = {}

        for symbol, result in analysis_results.items():
            if not result:
                continue

            sector = self.get_sector(symbol)
            if sector not in sectors:
                sectors[sector] = {
                    'symbols': [],
                    'changes': [],
                    'rsis': [],
                    'bullish': 0,
                    'bearish': 0,
                    'total': 0,
                }

            sec = sectors[sector]
            sec['symbols'].append(symbol)
            sec['total'] += 1

            # Daily change
            chg = result.get('daily_change_pct')
            if chg is not None:
                sec['changes'].append(chg)

            # RSI
            rsi = result.get('latest', {}).get('rsi')
            if rsi is not None:
                sec['rsis'].append(rsi)

            # Signal
            overall = result.get('signals', {}).get('overall', 'neutral')
            if 'bullish' in str(overall):
                sec['bullish'] += 1
            elif 'bearish' in str(overall):
                sec['bearish'] += 1

        # Compute aggregates
        result = {}
        for sector, data in sectors.items():
            avg_change = (
                sum(data['changes']) / len(data['changes'])
                if data['changes'] else 0.0
            )
            avg_rsi = (
                sum(data['rsis']) / len(data['rsis'])
                if data['rsis'] else None
            )
            bullish_ratio = (
                data['bullish'] / data['total'] * 100
                if data['total'] > 0 else 0
            )

            result[sector] = {
                'symbols': data['symbols'],
                'count': data['total'],
                'avg_change_pct': round(float(avg_change), 2),
                'avg_rsi': round(float(avg_rsi), 1) if avg_rsi else None,
                'bullish_ratio': round(float(bullish_ratio), 1),
                'bullish_count': data['bullish'],
                'bearish_count': data['bearish'],
            }

        # Sort by performance
        result = dict(
            sorted(result.items(),
                   key=lambda x: x[1]['avg_change_pct'], reverse=True)
        )
        return result

    def rank_sectors(self, sector_data):
        """
        Rank sectors by average daily change.

        Returns:
            list of (sector, avg_change_pct) tuples, best first.
        """
        ranked = [
            (s, d['avg_change_pct'])
            for s, d in sector_data.items()
        ]
        return sorted(ranked, key=lambda x: x[1], reverse=True)