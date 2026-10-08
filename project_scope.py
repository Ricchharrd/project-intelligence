"""Narrow the existing Czech rail watch without deleting historical records."""
import unicodedata
from sqlalchemy import text

MORAVIA_NAME = 'Moravia Gate 고속철도 (Moravská brána)'
MORAVIA_ALIASES = 'Moravia Gate; Moravian Gate; VRT Moravská brána; Moravska brana'


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value)).lower()
                   if not unicodedata.combining(c))


def mentions_moravia(value):
    value = normalized(value)
    return any(word in value for word in ('moravia gate', 'moravian gate', 'moravska brana',
                                           '모라비아 관문', '모라비아 게이트', '모라비아 게이트웨이'))


def is_moravia_project(name, country):
    return country == '체코' and (mentions_moravia(name) or name == 'High-Speed Rail (고속철도)')


def migrate_moravia(engine):
    # Exact legacy name/country match avoids rewriting unrelated projects or aliases.
    with engine.begin() as conn:
        conn.execute(text('''UPDATE projects SET name=:new,
            aliases=CASE WHEN aliases='' THEN :aliases ELSE aliases || '; ' || :aliases END
            WHERE country=:country AND name=:old'''),
            {'new': MORAVIA_NAME, 'aliases': MORAVIA_ALIASES,
             'country': '체코', 'old': 'High-Speed Rail (고속철도)'})


def visible_news(frame):
    """Conservative legacy gate. Historical articles remain in the management page."""
    if frame.empty:
        return frame
    return frame[frame.apply(lambda r: not is_moravia_project(r['project_name'], r['country'])
                             or mentions_moravia(r['title']), axis=1)].copy()


def scope_instructions(name, country):
    if not is_moravia_project(name, country):
        return ''
    return (' 검색 범위는 체코 VRT Moravská brána (Moravia Gate) 구간으로만 한정한다. '
            '체코 고속철도 전체 예산, 다른 구간, 지명의 단순 언급은 제외한다. '
            '이 구간의 조달·PPP·설계·토지·인허가·공사·재원에 직접 발생한 변화만 채택한다. '
            '구간 관련성이 확인되면 제목에 Moravia Gate를 명시하고 scope_match=true, '
            'scope_evidence에 원문의 구간명과 직접 관련 내용을 짧게 기록한다. '
            '직접 관련 근거가 없으면 found=false와 scope_match=false로 반환한다.')


def validate_scope(candidate, name, country):
    if candidate.get('found') and is_moravia_project(name, country):
        if (candidate.get('scope_match') is not True
                or not mentions_moravia(candidate.get('scope_evidence', ''))
                or not mentions_moravia(candidate.get('title', ''))):
            return {'found': False, 'reason': 'Moravia Gate 구간 관련 근거 부족'}
    return candidate
