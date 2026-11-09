import re
from num2words import num2words
import cn2an


def convert_numbers_in_text(text, target_lang='es'):
    """
    在文本中查找所有数字并将其转换为指定语言的文字。

    Args:
        text (str): 原始大段提示词。
        target_lang (str): 目标语言代码 ('zh', 'es', 'en' 等)。

    Returns:
        str: 替换完成后的文本。
    """

    # --- 1. 改进的正则 ---
    # 这个正则专门用来匹配带千分位逗号的数字 (如 1,000,000)
    # 或者普通数字 (1000)
    # 解释：
    # -?                 : 可选负号
    # \d{1,3}(?:,\d{3})+ : 匹配像 1,000 或 10,000,000 这样的格式
    # |                  : 或者
    # \d+                : 匹配不带逗号的普通数字
    # (?:\.\d+)?         : 可选的小数部分

    if target_lang == 'zh':
        return cn2an.transform(text, "an2cn")
    else:
        pattern = r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'

        def callback(match):
            original_str = match.group()

            # ============================================
            # 【关键修复】在这里去掉逗号
            # ============================================
            clean_str = original_str.replace(',', '')

            try:
                # 判断是否为小数
                if '.' in clean_str:
                    number = float(clean_str)
                else:
                    number = int(clean_str)

                # 转换为文字
                return num2words(number, lang=target_lang)

            except Exception as e:
                print(f"转换跳过: {original_str} -> {e}")
                return original_str

        # --- 3. 执行替换 ---
        # re.sub(正则, 回调函数, 原始文本)
        result = re.sub(pattern, callback, text)

    return result


if __name__ == '__main__':
    es_text = "En 2024, hay 2 usuarios con 1000000 euros."
    zh_text = "在2024年，有2个用户存入了1000000。"

    print(convert_numbers_in_text(es_text, 'es'))
    print(convert_numbers_in_text(zh_text, 'zh'))

