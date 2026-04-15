import os
import time
import pickle
import logging
import requests
import pandas as pd
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import Optional, List, Dict, Union
import re
import datetime

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) '
    'Version/18.4 Safari/605.1.15'
)

class SnowboardSession(requests.Session):
    URL = 'https://snowboard.sookmyung.ac.kr'

    def __init__(self, username, password, path_cookie: Path = Path('tmp/cookies.tmp'), timeout=30):
        super().__init__()
        self.username = username
        self.password = password
        self.path_cookie = path_cookie
        self.timeout = timeout
        self.headers['User-Agent'] = DEFAULT_USER_AGENT

        if self.path_cookie.parent:
            self.path_cookie.parent.mkdir(parents=True, exist_ok=True)

        if self.path_cookie.exists():
            try:
                self.cookies = pickle.loads(self.path_cookie.read_bytes())
            except Exception:
                logger.warning(f"Failed to load cookies from {self.path_cookie}")

    def login(self):
        response = super().get(self.URL, timeout=self.timeout)

        if self.is_logged_in(response):
            logger.debug('Already logged in.')
            return

        logger.info('Logging into SnowBoard...')
        bs = BeautifulSoup(response.text, 'html.parser')

        login_div = bs.find('div', {'class': 'textform'})
        if not login_div:
            logger.error("Login form not found.")
            raise RuntimeError("Login form not found on Snowboard page.")
            
        inputs = login_div.find_all('input')
        data = {i['name']: i['value'] for i in inputs if 'name' in i.attrs}
        data['username'] = self.username
        data['password'] = self.password

        response = super().post(
            self.URL + '/login/index.php', 
            data=data, 
            timeout=self.timeout
        )
        
        if not self.is_logged_in(response):
            raise AssertionError('Login failed. Please check credentials.')

        try:
            with self.path_cookie.open('wb') as fp:
                pickle.dump(self.cookies, fp)
        except Exception as e:
            logger.warning(f"Failed to save cookies: {e}")

    def is_logged_in(self, response=None):
        if response is None:
            response = super().get(self.URL, timeout=self.timeout)
        
        bs = BeautifulSoup(response.text, 'html.parser')
        title_tag = bs.find('title')
        title = title_tag.text if title_tag else ""

        if '사이트에 로그인' in title:
            return False
        return True

    def _request_wrapper(self, method, url, *args, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        func = getattr(super(), method)
        
        for trycount in range(3):
            response = func(url, *args, **kwargs)
            if self.is_logged_in(response):
                return response
            else:
                logger.info("Session expired (or not logged in), retrying login...")
                self.login()
        
        logger.error(f'Failed to {method} {url} after 3 login attempts.')
        raise AssertionError('Cannot log in to Snowboard.')

    def get(self, url, *args, **kwargs):
        return self._request_wrapper('get', url, *args, **kwargs)

    def post(self, url, *args, **kwargs):
        return self._request_wrapper('post', url, *args, **kwargs)


class SnowBoard:
    URL = 'https://snowboard.sookmyung.ac.kr'

    def __init__(self, user: str = None, password: str = None, cookie_path: Path = Path('tmp/cookies.tmp')):
        self.user = user or os.environ.get('SNOWBOARD_USER')
        self.password = password or os.environ.get('SNOWBOARD_PASSWORD')
        
        if not self.user or not self.password:
            raise ValueError("Snowboard credentials must be provided via arguments or env vars (SNOWBOARD_USER, SNOWBOARD_PASSWORD).")
            
        self.s = SnowboardSession(self.user, self.password, path_cookie=cookie_path)

    def list_assignments(self, id_lecture: str) -> pd.DataFrame:
        url = self.URL + f'/mod/assign/index.php?id={id_lecture}'
        
        for patience in range(3):
            try:
                response = self.s.get(url)
                bs = BeautifulSoup(response.text, 'html.parser')
                table = bs.find('table')
                if not table:
                    logger.warning(f"No assignment table found for lecture {id_lecture}")
                    return pd.DataFrame()
                    
                rows = table.find_all('tr')
                break
            except (AttributeError, requests.RequestException) as e:
                logger.warning(f'list_assignments failed ({patience+1}/3): {e}')
                time.sleep(1)
        else:
            raise RuntimeError(f"Failed to list assignments for {id_lecture}")

        if len(rows) < 2:
             return pd.DataFrame()

        headers = [th.text.strip() for th in rows[0].find_all('th')]
        data = [[td.text.strip() for td in tr.find_all('td')] for tr in rows[1:]]

        df = pd.DataFrame(data=data, columns=headers)

        raw_tds = [tr.find_all('td') for tr in rows[1:]]
        
        assignment_ids = []
        for tds in raw_tds:
            link_found = False
            for td in tds:
                a_tag = td.find('a')
                if a_tag and 'id=' in a_tag.get('href', ''):
                    href = a_tag['href']
                    aid = href.split('id=')[1]
                    assignment_ids.append(aid)
                    link_found = True
                    break
            if not link_found:
                assignment_ids.append(None)

        df['id_assignment'] = assignment_ids

        if '주' in df.columns:
            # 병합된 셀(빈 문자열로 표현)을 직전 값으로 채우기 위해 ffill.
            df['주'] = df['주'].replace('', pd.NA).ffill()

            # '종료 일시'에서 연도를 추출해 '주' 컬럼의 월/일과 결합.
            inferred_year = datetime.datetime.now().year
            if '종료 일시' in df.columns:
                valid_dt = df['종료 일시'][df['종료 일시'] != '-'].dropna()
                if not valid_dt.empty:
                    match = re.search(r'^(\d{4})', str(valid_dt.iloc[0]))
                    if match:
                        inferred_year = int(match.group(1))

            week_nums = []
            starts = []
            ends = []
            for x in df['주']:
                if pd.isna(x) or not isinstance(x, str):
                    week_nums.append(pd.NA)
                    starts.append(None)
                    ends.append(None)
                    continue
                
                week_num = x.split('주차')[0].strip() if '주차' in x else pd.NA
                week_nums.append(week_num)
                
                date_match = re.search(r'\[(\d+)월\s*(\d+)일\s*-\s*(\d+)월\s*(\d+)일\]', x)
                if date_match:
                    sm, sd, em, ed = date_match.groups()
                    starts.append(f"{inferred_year}-{int(sm):02d}-{int(sd):02d}")
                    ends.append(f"{inferred_year}-{int(em):02d}-{int(ed):02d}")
                else:
                    starts.append(None)
                    ends.append(None)
                    
            df['주'] = week_nums
            df['week_start'] = starts
            df['week_end'] = ends
        
        if '종료 일시' in df.columns:
            df = df[df['종료 일시'] != '-']

        return df

    def list_submissions(
        self,
        id_assignment: Union[int, str],
        rows_per_page: Union[int, str] = -1,
        filter_status: str = '',
        ascending: bool = False
    ) -> pd.DataFrame:
        # filter_status 가능값: 'requiregrading', 'submitted', ''
        tdir = 4 if ascending else 3
        url = (f'{self.URL}/mod/assign/view.php?action=grading'
               f'&id={id_assignment}&treset=1&tsort=timesubmitted&tdir={tdir}')
        
        response = self.s.get(url)
        bs = BeautifulSoup(response.text, 'html.parser')

        current_perpage = bs.find('select', {'id': 'id_perpage'})
        current_filter = bs.find('select', {'name': 'filter'})

        need_update = False

        if current_perpage:
            selected_perpage = current_perpage.select_one('option:checked')['value']
            if str(rows_per_page) != str(selected_perpage):
                need_update = True

        if current_filter and filter_status:
            selected_filter = current_filter.select_one('option:checked')['value']
            if filter_status != selected_filter:
                need_update = True
                
        if need_update:
            logger.info(f"Updating view options: perpage={rows_per_page}, filter={filter_status}")
            inputs = bs.find('form', {'class': 'gradingoptionsform mform'}).find_all('input')
            data = {i['name']: i['value'] for i in inputs if 'name' in i.attrs}
            data['perpage'] = str(rows_per_page)
            if filter_status:
                data['filter'] = filter_status
            data['quickgrading'] = 0
            
            response = self.s.post(self.URL + '/mod/assign/view.php', data=data)
            bs = BeautifulSoup(response.text, 'html.parser')

        table = bs.find('table')
        if not table:
            return pd.DataFrame()

        rows = table.find_all('tr')
        headers_raw = [th.text.strip() for th in rows[0].find_all('th')]
        headers = []
        for h in headers_raw:
            if '학번' in h: headers.append('학번')
            elif '이름' in h: headers.append('이름')
            elif '이메일' in h: headers.append('이메일')
            elif '상태' in h: headers.append('상태')
            elif '제출일' in h: headers.append('최근 제출일')
            elif '최종 수정' in h: headers.append('최종 수정')
            elif '파일' in h or 'File' in h: headers.append('첨부파일')
            else: headers.append(h)

        data = []
        valid_rows = [r for r in rows if 'class' in r.attrs and 'emptyrow' not in r.attrs['class']]

        for row in valid_rows:
            tds = row.find_all('td')
            valid_tds = [td for td in tds if 'id' in td.attrs and td.attrs['id'].startswith('mod_assign_grading')]
            data.append(valid_tds)

        list_of_dicts = []
        for i, row_tds in enumerate(data):
            row_dict = {}
            for j, td in enumerate(row_tds):
                if j < len(headers):
                    col_name = headers[j]
                    row_dict[col_name] = td.text.strip()
                    if '첨부파일' in col_name or 'File' in col_name:
                        a_tag = td.find('a')
                        if a_tag:
                            row_dict['첨부파일명'] = a_tag.text.strip()
                            row_dict['첨부파일href'] = a_tag['href']
                    if '성적' in col_name:
                         a_tag = td.find('a')
                         if a_tag:
                             row_dict['성적버튼href'] = a_tag['href']

            tr_orig = valid_rows[i]
            dropdown = tr_orig.find('div', {'class': 'dropdown-menu'})
            if dropdown:
                links = dropdown.find_all('a')
                if len(links) > 1:
                    row_dict['제출변경방지href'] = links[1]['href']
            
            list_of_dicts.append(row_dict)

        df = pd.DataFrame(list_of_dicts)
        df['id_assignment'] = id_assignment

        # 성적 컬럼의 "score / max" 표기에서 max 부분을 추출. 못 찾으면 기본값 100.
        max_score = 100.0

        grade_col = None
        for col in df.columns:
            if '성적' in col or 'Grade' in col:
                grade_col = col
                break

        if grade_col and not df.empty:
            for val in df[grade_col]:
                if isinstance(val, str) and '/' in val:
                    parts = val.split('/')
                    if len(parts) >= 2:
                        try:
                            candidate = float(parts[1].strip())
                            if candidate > 0:
                                max_score = candidate
                                break
                        except ValueError:
                            continue

        df['max_score'] = max_score
        df = df.sort_values('최근 제출일', ascending=True).reset_index(drop=True)
        return df

    def fetch_submission(self, url: str) -> bytes:
        """Downloads submission content from URL."""
        for _ in range(3):
            try:
                response = self.s.get(url, timeout=10)
                return response.content
            except requests.Timeout:
                continue
        raise requests.Timeout("Failed to download submission after 3 retries")

    def download_submission(self, row: dict, dest_dir: Path) -> Path:
        """
        Downloads submission to a specified directory.
        row must contain: '이름', '학번', '최근 제출일' (or formatted), '첨부파일명', '첨부파일href'
        """
        if not row.get('첨부파일href'):
            return None

        name = row.get('이름', 'Unknown')
        sid = row.get('학번', 'Unknown')

        ts_str = row.get('최근 제출일', '0000-00-00')

        ts_safe = ts_str.replace(':', '').replace(' ', '_')

        fname = row.get('첨부파일명', 'submission.py')
        ext = Path(fname).suffix

        save_name = f"{ts_safe}_{name}_{sid}{ext}"
        save_path = dest_dir / save_name
        
        content = self.fetch_submission(row['첨부파일href'])
        
        dest_dir.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(content)
        
        return save_path

    def fetch_instruction(self, assignment_id: Union[int, str]) -> dict:
        """
        Fetches assignment instruction page from Snowboard.
        Returns dict with 'title', 'html' (raw description HTML), and 'text' (cleaned text).
        Also downloads embedded images to downloaded_instructions/images/.
        """
        url = f"{self.URL}/mod/assign/view.php?id={assignment_id}"
        response = self.s.get(url)
        bs = BeautifulSoup(response.text, 'html.parser')

        title = ""
        header = bs.find('h2')
        if header:
            title = header.get_text(strip=True)

        intro_div = bs.find('div', {'id': 'intro'})
        if not intro_div:
            intro_div = bs.find('div', class_='assignmentbody')
        if not intro_div:
            intro_div = bs.find('div', class_='no-overflow')

        html_content = ""
        if intro_div:
            images_dir = Path("downloaded_instructions/images")
            images_dir.mkdir(parents=True, exist_ok=True)

            for img in intro_div.find_all('img'):
                src = img.get('src', '')
                if not src:
                    continue
                abs_url = urljoin(url, src)
                from urllib.parse import urlparse
                parsed = urlparse(abs_url)
                img_name = f"{assignment_id}_{Path(parsed.path).name}"
                local_path = images_dir / img_name

                if not local_path.exists():
                    try:
                        img_resp = self.s.get(abs_url, timeout=10)
                        local_path.write_bytes(img_resp.content)
                        logger.info(f"Downloaded image: {local_path}")
                    except Exception as e:
                        logger.warning(f"Failed to download image {abs_url}: {e}")

                # 다운로드한 로컬 이미지로 src를 다시 쓴다 (오프라인 보존용).
                img['src'] = f"images/{img_name}"

            html_content = str(intro_div)

        return {
            'title': title,
            'html': html_content,
            'assignment_id': str(assignment_id),
        }

    def save_instruction(self, assignment_id: Union[int, str],
                         dest_dir: Path = Path("downloaded_instructions")) -> Path:
        """
        Fetches and saves assignment instruction as markdown file.
        Returns the path to the saved file.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        info = self.fetch_instruction(assignment_id)
        title = info['title'] or f"Assignment {assignment_id}"
        html = info['html']

        content = f"# {title} (ID: {assignment_id})\n\n{html}\n"

        save_path = dest_dir / f"{assignment_id}.md"
        save_path.write_text(content, encoding='utf-8')
        logger.info(f"Saved instruction to {save_path}")
        return save_path

    def submit_score(self, grade_url: str, score: float, comment: str) -> bool:
        """
        Submits score using the 'grade_url' (extracted as '성적버튼href').
        """
        if not grade_url:
            return False
            
        response = self.s.get(grade_url)
        bs = BeautifulSoup(response.text, 'html.parser')

        inputs = bs.find('form', {'class': 'gradeform mform'}).find_all('input')

        data = {i['name']: i['value'] for i in inputs if 'name' in i.attrs}

        # 충돌 가능한 submit 버튼들은 제거 — 기본 save 버튼만 남겨야 응답이 깔끔하다.
        buttons_to_remove = [
            'saveandshownext', 'cancelbutton', 'nosaveandnext', 'nosaveandprevious'
        ]
        for btn in buttons_to_remove:
            if btn in data:
                del data[btn]

        data['grade'] = f'{score:.02f}'
        data['assignfeedbackcomments_editor[text]'] = comment.replace('\n', '<br>')

        form_action = bs.find('form', {'class': 'gradeform mform'}).get('action')
        if form_action:
            action = urljoin(grade_url, form_action)
        else:
            action = grade_url

        headers = {'Referer': grade_url}
        response = self.s.post(action, data=data, headers=headers)

        if '성적 변경 사항이 저장되었습니다' in response.text:
            return True

        soup = BeautifulSoup(response.text, 'html.parser')

        alert_div = soup.find('div', class_='alert-success')
        success_msg = "성적 변경 사항이 저장되었습니다"

        if alert_div and success_msg in alert_div.get_text():
             logger.info("  Found success message in alert-success.")
             return True

        error_div = soup.find('div', class_='alert-danger')
        if error_div:
            logger.error(f"  Snowboard Error: {error_div.get_text(strip=True)}")
            return False
            
        error_msg = soup.find(class_='error')
        if error_msg:
            logger.error(f"  Snowboard Error Class: {error_msg.get_text(strip=True)}")
            return False

        # 성공/실패 알림이 모두 없으면 "변경 없음"으로 간주 (Snowboard의 동일 점수 재제출 동작).
        logger.info("  Success message not found, but no errors detected. Assuming 'No Change' success.")
        return True

    def lock_submission(self, lock_url: str):
        """
        Locks a student's submission to prevent further submissions.
        Called after a student achieves max score (제출변경방지).
        """
        if not lock_url:
            return
        self.s.get(lock_url)
