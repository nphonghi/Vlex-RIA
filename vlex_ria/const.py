"""
Vlex-RIA Constants and System Configurations.
"""

VLEX_RIA_LOGO = r"""
 __      ___             _____  _____          
 \ \    / / |           |  __ \|_   _|   /\    
  \ \  / /| | _____  __ | |__) | | |    /  \   
   \ \/ / | |/ _ \ \/ / |  _  /  | |   / /\ \  
    \  /  | |  __/>  <  | | \ \ _| |_ / ____ \ 
     \/   |_|\___/_/\_\ |_|  \_\_____/_/    \_\
               Hệ thống Đánh giá Tác động Chính sách
"""

VLEX_RIA_TITLE = "Vlex-RIA - Hệ thống Tự động Đánh giá Tác động Chính sách"

LEGAL_DOCUMENT_TYPES = [
    "Luật",
    "Nghị quyết của Quốc hội",
    "Pháp lệnh",
    "Nghị định của Chính phủ",
    "Quyết định của Thủ tướng",
    "Thông tư",
]

AREA_CHAIR_TYPES = ['inclusive', 'conformist', 'authoritarian', 'BASELINE']

GLOBAL_PROMPT = "Mô phỏng hội đồng thẩm định đánh giá tác động chính sách (RIA) theo Mẫu số 01 Nghị định Chính phủ."

ALL_REVIEW_PHASES = [
    "document_extraction",
    "reviewer_write_reviews",
    "author_reviewer_discussion",
    "reviewer_ac_discussion",
    "ac_write_metareviews",
    "ac_makes_decisions",
]
