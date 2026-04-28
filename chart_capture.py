import os
import base64
import mimetypes
from datetime import datetime
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def capture_upbit_chart(url="https://www.upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC"):
    """Upbit BTC 차트 페이지의 전체 화면 캡쳐를 저장합니다."""
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        print("Chrome 드라이버 시작...")

        driver.get(url)
        print(f"접속 중: {url}")

        print("페이지 로딩 대기 중...")
        wait = WebDriverWait(driver, 20)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            print("Body 로드 완료")
        except Exception as e:
            print(f"대기 중 오류: {e}, 계속 진행...")

        print("차트 렌더링 대기 중...")
        time.sleep(5)

        print("1시간 차트로 변경 중...")
        try:
            timeframe_menu_xpath = "/html/body/div[1]/div/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/span/cq-clickable"
            one_hour_xpath = "/html/body/div[1]/div/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]"

            timeframe_menu_button = driver.find_element(By.XPATH, timeframe_menu_xpath)
            driver.execute_script("arguments[0].scrollIntoView(true);", timeframe_menu_button)
            time.sleep(1)
            timeframe_menu_button.click()
            print("타임프레임 메뉴 클릭 완료")

            time.sleep(1)
            one_hour_button = driver.find_element(By.XPATH, one_hour_xpath)
            driver.execute_script("arguments[0].scrollIntoView(true);", one_hour_button)
            time.sleep(0.5)
            one_hour_button.click()
            print("1시간 버튼 클릭 완료")

            time.sleep(3)
        except Exception as e:
            print(f"타임프레임 변경 중 오류 (계속 진행): {e}")

        print("볼린저밴드 지표 추가 중...")
        try:
            indicator_button_xpath = "/html/body/div[1]/div/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/span"
            bollinger_xpath = "/html/body/div[1]/div/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[14]"

            indicator_button = driver.find_element(By.XPATH, indicator_button_xpath)
            driver.execute_script("arguments[0].scrollIntoView(true);", indicator_button)
            time.sleep(0.5)
            indicator_button.click()
            print("지표 버튼 클릭 완료")

            time.sleep(1)
            bollinger_button = driver.find_element(By.XPATH, bollinger_xpath)
            driver.execute_script("arguments[0].scrollIntoView(true);", bollinger_button)
            time.sleep(0.5)
            bollinger_button.click()
            print("볼린저밴드 지표 추가 완료")

            time.sleep(3)
        except Exception as e:
            print(f"지표 추가 중 오류 (계속 진행): {e}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"upbit_btc_chart_{timestamp}.png"
        filepath = os.path.join(os.getcwd(), filename)

        total_height = driver.execute_script("return document.documentElement.scrollHeight")
        total_width = driver.execute_script("return document.documentElement.scrollWidth")
        print(f"페이지 크기: {total_width}x{total_height}")

        driver.set_window_size(total_width, total_height + 100)
        time.sleep(2)

        driver.save_screenshot(filepath)
        print(f"캡쳐 완료: {filepath}")
        print(f"파일 크기: {os.path.getsize(filepath) / 1024:.2f} KB")

        return filepath
    except Exception as e:
        print(f"캡쳐 중 오류 발생: {e}")
        return None
    finally:
        if driver is not None:
            driver.quit()
            print("드라이버 종료")


def image_file_to_data_url(image_path):
    """OpenAI Vision 입력용 data URL(base64 인코딩 문자열) 생성"""
    if not image_path or not os.path.exists(image_path):
        return None

    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/png"

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"
