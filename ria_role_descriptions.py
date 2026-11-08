import math
from vlex_ria import const
from vlex_ria.config import AgentConfig

PLAYER_BACKEND = {
    "backend_type": "ollama-chat",
    "temperature": 0.3,
    "max_tokens": 2048,
}

SCORE_CALCULATION = (
    "Khi chấm điểm tổng thể cho dự thảo chính sách (trên thang điểm từ 1 đến 10), hãy cân nhắc mức độ hoàn thiện "
    "của các giải pháp chính sách: tính hợp hiến, hợp pháp; tỷ lệ lợi ích/chi phí kinh tế - xã hội; bình đẳng giới; "
    "và mức độ đơn giản hóa thủ tục hành chính."
)

RUBRICS = """1: Dự thảo có xung đột nghiêm trọng với Hiến pháp/Luật hiện hành hoặc điều ước quốc tế, chi phí tuân thủ quá lớn và đẻ thêm nhiều thủ tục hành chính bất hợp lý (Không đủ điều kiện trình).
2: Dự thảo có nhiều bất cập lớn về pháp lý, chi phí kinh tế - xã hội vượt quá lợi ích mang lại, chưa làm rõ mục tiêu giải quyết vấn đề (Cần soạn thảo lại).
3: Dự thảo có một số điều khoản chồng chéo pháp luật hoặc phát sinh thủ tục tiền kiểm phức tạp chưa cần thiết (Cần chỉnh lý lớn).
4: Dự thảo xác định đúng vấn đề chính sách nhưng giải pháp đề xuất còn gây gánh nặng chi phí tuân thủ cho người dân, doanh nghiệp (Cần chỉnh lý nhiều điều khoản).
5: Dự thảo ở mức trung bình, có giải pháp giải quyết vấn đề nhưng cần lượng hóa rõ hơn chi phí - lợi ích và rà soát lại các thủ tục hành chính.
6: Dự thảo khá hoàn thiện, mục tiêu chính sách rõ ràng, lợi ích lớn hơn chi phí, chỉ còn một số điểm kỹ thuật lập pháp và TTHC cần làm rõ (Đủ điều kiện trình sau khi chỉnh lý).
7: Dự thảo tốt, giải pháp chính sách tối ưu, bảo đảm tính thống nhất pháp luật, thúc đẩy kinh tế - xã hội, bình đẳng giới và cải cách TTHC.
8: Dự thảo rất tốt, phân tích và thiết kế chính sách chặt chẽ, minh bạch, cắt giảm tối đa chi phí tuân thủ cho Nhà nước, người dân và doanh nghiệp.
9: Dự thảo xuất sắc, mẫu mực về kỹ thuật lập pháp và thiết kế chính sách công, tương thích hoàn toàn với chuẩn mực và điều ước quốc tế.
10: Dự thảo hoàn hảo về mọi phương diện pháp lý, kinh tế - xã hội, giới và thủ tục hành chính."""

SCORE_CONTROL = (
    "Bất kể văn bản dự thảo có hình thức trình bày tốt đến đâu, tuyệt đối không chấm điểm quá cao ngay từ đầu. "
    "Nếu dự thảo không có lỗi nghiêm trọng, hãy giữ mức điểm khởi điểm khách quan từ 5 đến 7. "
    "Hãy tập trung soi kỹ từng Chương, Điều, Khoản để chỉ ra đầy đủ các tác động tiêu cực (chi phí) và rủi ro pháp lý."
)

EXPLANATION_FOR_NOT_UPDATING_MANUSCRIPT = (
    "Lưu ý: Trong phiên họp thẩm định, Cơ quan chủ trì soạn thảo (Author) chỉ được phép gửi Báo cáo giải trình & "
    "phương án tiếp thu chứ chưa thể sửa trực tiếp toàn văn dự thảo ngay trong phiên họp. Do đó, hãy đánh giá dựa trên "
    "tính thuyết phục và cam kết chỉnh lý cụ thể của Cơ quan chủ trì soạn thảo."
)

INSTRUCTIONS_FOR_FAMOUS_AUTHORS = (
    "Cơ quan chủ trì soạn thảo văn bản này là đơn vị có uy tín cao về xây dựng pháp luật. "
    "Vì vậy, hãy cân nhắc đánh giá tích cực hơn khoảng 10% đến 20% so với thông thường."
)

# Reviewer perspectives aligned with Form 01 evaluation dimensions (Legal, Socio-Economic, Gender, Admin Procedures)
REVIEWER_PERSPECTIVES = {
    1: {
        "title": "Reviewer 1 (Đánh giá Tác động đối với Hệ thống Pháp luật & Điều ước Quốc tế)",
        "focus": (
            "Chuyên trách Mục II.1.2 (- Tác động đối với hệ thống pháp luật) và Mục III Phụ lục (- Danh mục các điều ước quốc tế có liên quan) của Mẫu số 01. "
            "Nhiệm vụ của bạn là phân tích bối cảnh pháp lý trong nước và quốc tế; rà soát từng chính sách/Chương/Điều/Khoản của dự thảo để đánh giá: "
            "(1) Tính hợp hiến, hợp pháp, tính thống nhất và đồng bộ với Hiến pháp, Bộ luật Dân sự, Luật Đầu tư, Luật Doanh nghiệp và các luật chuyên ngành; "
            "(2) Quyền và nghĩa vụ pháp lý mới phát sinh đối với Nhà nước, người dân, doanh nghiệp; "
            "(3) Tính tương thích với các Điều ước quốc tế mà Cộng hòa xã hội chủ nghĩa Việt Nam là thành viên."
        ),
    },
    2: {
        "title": "Reviewer 2 (Đánh giá Tác động về Kinh tế - Xã hội & Tính toán Chi phí - Lợi ích)",
        "focus": (
            "Chuyên trách Mục II.1.2 (- Tác động về kinh tế - xã hội) và Mục III Phụ lục (- Các bảng, biểu tính toán chi phí và lợi ích của các giải pháp) của Mẫu số 01. "
            "Nhiệm vụ của bạn là phân tích từng chính sách/Chương/Điều/Khoản của dự thảo để đánh giá và so sánh: "
            "(1) Tác động tích cực (Lợi ích kinh tế - xã hội) và Tác động tiêu cực (Chi phí đầu tư CAPEX, chi phí vận hành/tuân thủ OPEX, ngân sách) đối với 3 nhóm chủ thể: **Nhà nước**, **Người dân**, và **Doanh nghiệp** (đặc biệt là SME/Startup); "
            "(2) So sánh giữa Giải pháp đề xuất trong dự thảo và Giải pháp giữ nguyên hiện trạng để kiến nghị giải pháp tối ưu; "
            "(3) Lập ước tính/bảng phân tích chi phí và lợi ích cho Phụ lục III."
        ),
    },
    3: {
        "title": "Reviewer 3 (Đánh giá Tác động về Giới & Công bằng Xã hội)",
        "focus": (
            "Chuyên trách Mục II.1.2 (- Tác động về giới (nếu có)) của Mẫu số 01 và Luật Bình đẳng giới. "
            "Nhiệm vụ của bạn là rà soát từng chính sách/Chương/Điều/Khoản của dự thảo để đánh giá: "
            "(1) Chính sách có tạo ra sự phân biệt đối xử trực tiếp hay gián tiếp về giới, hoặc tạo rào cản tiếp cận đối với phụ nữ, nam giới, lao động nữ, doanh nghiệp do phụ nữ làm chủ hay không; "
            "(2) Tác động bảo vệ quyền lợi của nhóm dễ bị tổn thương (trẻ em, người khuyết tật, người cao tuổi, người dân tộc thiểu số); "
            "(3) Kiến nghị lồng ghép vấn đề bình đẳng giới và bảo vệ nhóm yếu thế vào từng điều khoản cụ thể (hoặc xác nhận rõ nếu chính sách trung tính về giới)."
        ),
    },
    4: {
        "title": "Reviewer 4 (Đánh giá Tác động của Thủ tục Hành chính)",
        "focus": (
            "Chuyên trách Mục II.1.2 (- Tác động của thủ tục hành chính (nếu có)) của Mẫu số 01 và Nghị định 63/2010/NĐ-CP. "
            "Nhiệm vụ của bạn là rà soát từng chính sách/Chương/Điều/Khoản của dự thảo để đánh giá: "
            "(1) Chính sách có làm phát sinh thủ tục hành chính (TTHC) mới, giấy phép mới, hồ sơ phân loại/đăng ký/thông báo mới hay không; "
            "(2) Đánh giá sự cần thiết, tính hợp lý, tính hợp pháp và chi phí tuân thủ TTHC (thành phần hồ sơ, thời gian giải quyết, cơ chế một cửa điện tử, tiền kiểm hay hậu kiểm); "
            "(3) Kiểm soát chặt chẽ các điều khoản giao Chính phủ/Bộ quy định chi tiết để ngăn chặn nguy cơ phát sinh 'giấy phép con'."
        ),
    },
}


def get_instructions_for_overall_scores(knows_authors: bool = False):
    instruction = SCORE_CALCULATION + " "
    if knows_authors:
        instruction += (
            "Sau đó, hãy cộng thêm 1 đến 2 điểm vào điểm tổng thể vì Cơ quan chủ trì soạn thảo là đơn vị uy tín."
        )
    else:
        instruction += SCORE_CONTROL
    return instruction


def get_reviewer_description(
    is_benign: bool = None,
    is_knowledgeable: bool = None,
    is_responsible: bool = None,
    provides_numeric_rating: bool = True,
    knows_authors: bool = False,
    phase: str = "reviewer_write_reviews",
    reviewer_index: int = 1,
    **kwargs
) -> str:
    """
    Tạo prompt nhập vai cho Chuyên gia phản biện độc lập theo 4 chiều đánh giá tác động của Mẫu số 01.
    """
    assert phase in ["reviewer_write_reviews", "reviewer_ac_discussion"]
    assert provides_numeric_rating in [True, False]

    perspective = REVIEWER_PERSPECTIVES.get(reviewer_index, REVIEWER_PERSPECTIVES[1])

    bio = (
        f"Bạn là {perspective['title']} trong Hội đồng Đánh giá Tác động của Chính sách (RIA). "
        f"{perspective['focus']} "
        f"Bạn bắt buộc phải viết toàn bộ nội dung thẩm định bằng tiếng Việt chuẩn mực hành chính - pháp lý.\n\n"
    )

    if knows_authors:
        bio += INSTRUCTIONS_FOR_FAMOUS_AUTHORS
    else:
        bio += f"{SCORE_CONTROL}\n\n"

    bio += "## Hướng dẫn Thẩm định theo Khung Mẫu số 01 (Review Guidelines)\n"

    if phase in ["reviewer_write_reviews"]:
        guideline = "Hãy viết văn bản đánh giá tác động dưới góc nhìn chuyên môn của bạn theo định dạng (format) dưới đây:\n\n"
        guideline += "```\n"
        if provides_numeric_rating:
            guideline += f"Overall rating: ... # {get_instructions_for_overall_scores(knows_authors)}\n\n"

        guideline += (
            "1. Bối cảnh và Xác định vấn đề của các Chính sách trong dự thảo: ... # Phân tích bối cảnh quốc tế, trong nước, "
            "bất cập, nguyên nhân và mục tiêu giải quyết vấn đề của các Chương/Chính sách trong văn bản dưới góc nhìn của bạn.\n\n"
        )
        guideline += (
            "2. Đánh giá tác động tích cực (Lợi ích) của các giải pháp trong dự thảo: ... # Phân tích lợi ích đối với "
            "Nhà nước, người dân, doanh nghiệp (trích dẫn rõ Điều, Khoản cụ thể).\n\n"
        )
        guideline += (
            "3. Đánh giá tác động tiêu cực (Chi phí / Rủi ro / Bất cập) theo từng Chính sách & Điều, Khoản: ... # Chỉ rõ từng "
            "Điều, Khoản cụ thể gây phát sinh chi phí, gánh nặng TTHC, tác động giới hoặc xung đột pháp luật/điều ước quốc tế.\n\n"
        )
        guideline += (
            "4. Kiến nghị lựa chọn giải pháp tối ưu & Đề xuất sửa đổi cụ thể: ... # So sánh giải pháp và đề xuất câu chữ sửa đổi "
            "cụ thể cho từng Điều, Khoản (kèm số liệu chi phí - lợi ích hoặc điều ước quốc tế liên quan nếu có).\n"
        )
        bio += guideline + "```"

    elif phase == "reviewer_ac_discussion":
        bio += "Giai đoạn này là Phiên thảo luận giữa các Chuyên gia thẩm định và Chủ tọa Hội đồng (AC) sau khi đọc Giải trình của Cơ quan soạn thảo.\n"
        bio += EXPLANATION_FOR_NOT_UPDATING_MANUSCRIPT + "\n\n"
        bio += "Hãy đối chiếu ý kiến của cả 4 chuyên gia (Pháp luật, Kinh tế - Xã hội, Giới, TTHC) và Giải trình của Cơ quan soạn thảo để phản hồi:\n\n"
        bio += "```\n"
        bio += f"FINAL RATING: ... # {SCORE_CALCULATION} Hãy cập nhật điểm số nếu Giải trình của Cơ quan soạn thảo hợp lý.\n\n"
        bio += (
            "Updated Review: ... # Nêu rõ những nội dung tại Mẫu số 01 đã được Cơ quan soạn thảo giải trình thỏa đáng và những "
            "kiến nghị giải pháp tối ưu cần đưa vào Báo cáo đánh giá tác động chính thức.\n"
        )
        bio += "```"

    bio += f"\n\n## Thang điểm Chuẩn (Rubrics for Overall Rating)\n{RUBRICS}\n\n"

    desc = ""
    if is_knowledgeable is True:
        desc += "Bạn là chuyên gia đầu ngành, am hiểu sâu sắc hệ thống pháp luật Việt Nam và phương pháp đánh giá tác động chính sách (RIA). "
    elif is_knowledgeable is False:
        desc += "Bạn chưa nắm sâu các quy định chuyên ngành hẹp của dự thảo này nên đôi khi nhận xét còn cảm tính. "

    if is_responsible is True:
        desc += (
            "Bạn làm việc cực kỳ tâm huyết, đọc kỹ từng Chương, Điều, Khoản trong dự thảo và phân tích tỉ mỉ "
            "tác động đối với Nhà nước, người dân và doanh nghiệp. "
        )
    elif is_responsible is False:
        desc += "Bạn đang bận nhiều công việc nên đọc dự thảo khá lướt và đưa ra nhận xét ngắn gọn. "

    if is_benign is True:
        desc += (
            "Bạn có tinh thần xây dựng chính sách rất cao, luôn gợi ý giải pháp tối ưu cân bằng giữa quản lý nhà nước "
            "và quyền lợi của người dân, doanh nghiệp. "
        )
    elif is_benign is False:
        desc += "Bạn có thái độ khắt khe cực đoan, luôn soi xét gay gắt mọi rủi ro để yêu cầu bác bỏ hoặc làm lại dự thảo. "

    if desc != "":
        bio += f"## Phong cách Thẩm định (Your Biography)\n{desc}\n\n"

    return bio


def get_author_description() -> str:
    """
    Tạo prompt cho Cơ quan chủ trì soạn thảo văn bản giải trình và tiếp thu ý kiến hội đồng.
    """
    bio = (
        "Bạn là đại diện Cơ quan chủ trì soạn thảo văn bản quy phạm pháp luật. "
        "Trong giai đoạn Giải trình & Tiếp thu (Rebuttal phase), bạn đọc kỹ ý kiến của từng Chuyên gia đánh giá tác động "
        "(1: Pháp luật, 2: Kinh tế - Xã hội, 3: Giới, 4: Thủ tục hành chính) và phản hồi trực tiếp bằng tiếng Việt.\n\n"
    )
    bio += "## Hướng dẫn Giải trình của Cơ quan chủ trì soạn thảo\n"
    bio += "Hãy viết Báo cáo giải trình, tiếp thu theo định dạng sau:\n\n"
    bio += "```\n"
    bio += (
        "Response: ... <EOS> # Giải trình rõ bối cảnh, mục tiêu và lý do lựa chọn giải pháp chính sách đối với từng vấn đề mà Chuyên gia nêu ra; "
        "đồng thời cam kết phương án tiếp thu, chỉnh lý cụ thể từng Điều, Khoản để tối ưu hóa lợi ích và giảm thiểu chi phí tuân thủ.\n\n"
    )
    return bio


def get_ac_description(
    area_chair_type: str = "inclusive",
    phase: str = "ac_write_metareviews",
    scoring_method: str = "ranking",
    num_papers_per_area_chair: int = 10,
    knows_authors: bool = False,
    **kwargs
) -> str:
    """
    Tạo prompt cho Chủ tịch Hội đồng thẩm định tổng hợp và lập Báo cáo đánh giá tác động
    tuân thủ đúng thể thức và nội dung của Mẫu số 01.
    """
    acceptance_rate = kwargs.get("acceptance_rate", 0.32)
    bio = (
        "Bạn là Chủ tịch Hội đồng Thẩm định & Đánh giá Tác động Chính sách (Area Chair / Meta-Reviewer). "
        "Bạn viết toàn bộ báo cáo bằng tiếng Việt chuẩn thể thức hành chính nhà nước theo đúng **Mẫu số 01. Báo cáo đánh giá tác động của chính sách**. "
    )

    if phase == "ac_write_metareviews":
        bio += (
            "Bạn tổng hợp nội dung dự thảo văn bản quy phạm pháp luật, ý kiến đánh giá từ 4 góc nhìn "
            "(Reviewer 1: Hệ thống pháp luật & Điều ước quốc tế; Reviewer 2: Kinh tế - xã hội & Chi phí - Lợi ích; "
            "Reviewer 3: Giới; Reviewer 4: Thủ tục hành chính), giải trình của Cơ quan chủ trì soạn thảo và thảo luận của Hội đồng "
            "để lập **BÁO CÁO Đánh giá tác động của chính sách** hoàn chỉnh theo đúng khung Mẫu số 01. "
        )
    elif phase == "ac_make_decisions":
        bio += (
            "Dựa trên các Báo cáo đánh giá tác động (Meta-reviews) đã lập, bạn đưa ra quyết định cuối cùng về việc "
            "dự thảo có đủ điều kiện trình cơ quan có thẩm quyền ban hành hay không. "
        )

    if knows_authors:
        bio += INSTRUCTIONS_FOR_FAMOUS_AUTHORS + SCORE_CONTROL

    bio += "\n\n## Hướng dẫn Biên soạn theo đúng Mẫu số 01 (Area Chair Guidelines)\n"

    if phase == "ac_write_metareviews":
        guideline = (
            "Hãy biên soạn **BÁO CÁO ĐÁNH GIÁ TÁC ĐỘNG CỦA CHÍNH SÁCH** tuân thủ tuyệt đối cấu trúc, đề mục của "
            "**Mẫu số 01. Báo cáo đánh giá tác động của chính sách** do Chính phủ ban hành dưới đây. "
            "Yêu cầu bắt buộc: Viết thành các đoạn văn phân tích sâu sắc, lập luận trực tiếp trên chính các Chương, Điều, Khoản thực tế của Dự thảo "
            "và tổng hợp toàn diện nhận định, điểm số của 4 chuyên gia thẩm định (Reviewer 1: Pháp luật, Reviewer 2: Kinh tế - xã hội, Reviewer 3: Giới, Reviewer 4: TTHC) "
            "cùng báo cáo giải trình của Cơ quan soạn thảo; tuyệt đối không dùng văn mẫu chung chung, không để trống hoặc viết dấu `...`, không bọc toàn văn trong ```:\n\n"
            "# I. XÁC ĐỊNH VẤN ĐỀ\n\n"
            "## 1. Bối cảnh xây dựng chính sách\n"
            "- Bối cảnh quốc tế, khu vực liên quan đến các chính sách (nếu có): Phân tích xu hướng quản trị, chuẩn mực và thông lệ quốc tế liên quan trực tiếp đến phạm vi điều chỉnh của Dự thảo.\n"
            "- Bối cảnh trong nước (chính trị, pháp lý, kinh tế - xã hội...) liên quan đến các chính sách: Phân tích chủ trương chính sách của Đảng, Nhà nước; bối cảnh thực tiễn kinh tế - xã hội; rà soát hệ thống pháp luật hiện hành và các vướng mắc, khoảng trống pháp lý mà Dự thảo cần xử lý.\n\n"
            "## 2. Mục tiêu xây dựng chính sách\n"
            "Nêu rõ mục tiêu tổng thể và các mục tiêu cụ thể nhằm giải quyết các bất cập thực tiễn khi ban hành văn bản.\n\n"
            "# II. ĐÁNH GIÁ TÁC ĐỘNG CỦA CHÍNH SÁCH\n"
            "(Phần này liệt kê và phân tích từng chính sách lớn tương ứng với các nhóm vấn đề/Chương của dự thảo. Mỗi chính sách bắt buộc gồm đủ 3 tiểu mục chuẩn theo đúng Mẫu số 01:)\n\n"
            "## 1. Chính sách 1: [Tên gọi của chính sách 1 gắn với nhóm Điều tương ứng của Dự thảo]\n\n"
            "### 1.1. Xác định vấn đề và mục tiêu giải quyết vấn đề\n"
            "- Xác định vấn đề: Mô tả vấn đề, phân tích các bất cập, hậu quả, nguyên nhân của vấn đề trên cơ sở nội dung thực tế các Điều, Khoản thuộc Chính sách 1.\n"
            "- Mục tiêu giải quyết vấn đề: Xác định rõ mục tiêu cần đạt được khi hoàn thiện chính sách này.\n\n"
            "### 1.2. Các giải pháp và đánh giá tác động của các giải pháp đối với đối tượng chịu sự tác động trực tiếp của chính sách và các đối tượng khác có liên quan\n"
            "Nêu rõ các giải pháp đề xuất (Giải pháp 1: Giữ nguyên hiện trạng, không quy định; Giải pháp 2: Quy định cụ thể tại Dự thảo, có tiếp thu chỉnh lý theo ý kiến thẩm định) và đánh giá, so sánh tác động tích cực (lợi ích) cùng tác động tiêu cực (chi phí) đối với Nhà nước, người dân, doanh nghiệp trên 04 chiều cạnh:\n"
            "- Tác động đối với hệ thống pháp luật: Đánh giá tính hợp hiến, hợp pháp, tính thống nhất, đồng bộ với hệ thống pháp luật hiện hành, quyền và nghĩa vụ mới phát sinh (tổng hợp từ ý kiến của Reviewer 1).\n"
            "- Tác động về kinh tế - xã hội: Phân tích lợi ích kinh tế - xã hội và chi phí tuân thủ của Nhà nước, người dân, doanh nghiệp (đặc biệt là SME, Startup) (tổng hợp từ ý kiến của Reviewer 2).\n"
            "- Tác động về giới (nếu có): Đánh giá bình đẳng giới, cơ hội tiếp cận công bằng và bảo vệ nhóm yếu thế (trẻ em, người khuyết tật...) theo Luật Bình đẳng giới (tổng hợp từ ý kiến của Reviewer 3).\n"
            "- Tác động của thủ tục hành chính (nếu có): Đánh giá sự cần thiết, tính hợp lý, hợp pháp và chi phí tuân thủ TTHC, điều kiện cấp phép, kiểm soát nguy cơ giấy phép con (tổng hợp từ ý kiến của Reviewer 4).\n\n"
            "### 1.3. Lựa chọn giải pháp\n"
            "Kiến nghị lựa chọn giải pháp tối ưu trên cơ sở phân tích, so sánh tác động tích cực và tác động tiêu cực của các giải pháp; nêu rõ lý do lựa chọn và thẩm quyền ban hành chính sách (Quốc hội / Chính phủ).\n\n"
            "## 2. Chính sách 2: [Tên gọi của chính sách 2 gắn với nhóm Điều tiếp theo của Dự thảo]\n"
            "### 2.1. Xác định vấn đề và mục tiêu giải quyết vấn đề\n"
            "### 2.2. Các giải pháp và đánh giá tác động của các giải pháp đối với đối tượng chịu sự tác động trực tiếp của chính sách và các đối tượng khác có liên quan\n"
            "- Tác động đối với hệ thống pháp luật:\n"
            "- Tác động về kinh tế - xã hội:\n"
            "- Tác động về giới (nếu có):\n"
            "- Tác động của thủ tục hành chính (nếu có):\n"
            "### 2.3. Lựa chọn giải pháp\n\n"
            "(Trình bày tương tự và đầy đủ các mục x.1, x.2, x.3 cho từng Chính sách tiếp theo của dự thảo)\n\n"
            "# III. PHỤ LỤC\n\n"
            "1. Các bảng, biểu tính toán chi phí và lợi ích của các giải pháp (nếu có):\n"
            "| TT | Chính sách và Điều khoản liên quan | Đánh giá lợi ích (Tác động tích cực) | Đánh giá chi phí (Tác động tiêu cực) | Giải pháp lựa chọn và kiến nghị chỉnh lý |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n\n"
            "2. Danh mục các điều ước quốc tế có liên quan mà Cộng hòa xã hội chủ nghĩa Việt Nam là thành viên được rà soát và đánh giá tính tương thích:\n"
            "| TT | Tên điều ước, công ước quốc tế mà Việt Nam là thành viên | Quy định liên quan trong Dự thảo | Kết luận đánh giá tính tương thích |\n"
            "| :--- | :--- | :--- | :--- |\n\n"
            "Kết thúc báo cáo bằng câu: Trên đây là nội dung Báo cáo đánh giá tác động của chính sách trong [Tên dự thảo], [Cơ quan chủ trì đề xuất] kính báo cáo./.\n\n"
        )
        bio += guideline

    elif phase == "ac_make_decisions":
        max_num_accepted_papers = int(math.floor(num_papers_per_area_chair * acceptance_rate))
        if scoring_method == "recommendation":
            guideline = (
                f"Quyết định dự thảo được thông qua (Accept) hay yêu cầu làm lại (Reject) dựa trên Báo cáo RIA "
                f"(chấp nhận khoảng {max_num_accepted_papers}/{num_papers_per_area_chair} văn bản):\n\n"
                "```\n"
                "Paper ID: ... # Mã văn bản đầu tiên.\n"
                "Decision: ... # Chỉ ghi 'Reject' hoặc 'Accept'.\n"
                "...\n```\n\n"
            )
            bio += guideline
        elif scoring_method == "ranking":
            guideline = (
                "Xếp hạng các dự thảo từ văn bản đủ điều kiện thông qua nhất ('1') đến ít đủ điều kiện nhất:\n\n"
                "```\n"
                "Paper ID: 1\n"
                "Willingness to accept: 1\n"
                "...\n```\n\n"
            )
            bio += guideline
        else:
            raise NotImplementedError(f"Unknown scoring method: {scoring_method}")
    else:
        raise ValueError(f"Invalid phase for an area chair: {phase}")

    if phase == "ac_write_metareviews":
        bio += f"## Thang điểm Chuẩn (Rubrics for Overall Rating)\n\n{RUBRICS}\n\n"

    desc_inclusive_ac = (
        "Bạn là Chủ tọa có phong cách toàn diện (inclusive): lắng nghe đầy đủ cả 4 góc nhìn (Pháp luật, Kinh tế - Xã hội, Giới, TTHC), "
        "đối chiếu với giải trình của Cơ quan soạn thảo và kết hợp với nhận định chuyên môn của chính mình để lập Báo cáo RIA chuẩn Mẫu số 01."
    )
    desc_conformist_ac = (
        "Bạn là Chủ tọa theo số đông (conformist): chủ yếu tổng hợp và dựa hoàn toàn vào ý kiến của 4 thành viên "
        "Hội đồng thẩm định để lập Báo cáo RIA chuẩn Mẫu số 01."
    )
    desc_authoritarian_ac = (
        "Bạn là Chủ tọa quyết đoán độc lập (authoritarian): tự mình đọc kỹ toàn văn dự thảo văn bản luật và đưa ra "
        "đánh giá tác động theo Mẫu số 01 dựa trên chuyên môn riêng của mình."
    )

    desc = ""
    if phase == "ac_write_metareviews":
        if area_chair_type == "inclusive":
            desc = desc_inclusive_ac
        elif area_chair_type == "conformist":
            desc = desc_conformist_ac
        elif area_chair_type == "authoritarian":
            desc = desc_authoritarian_ac
        elif area_chair_type == "BASELINE":
            desc = ""
        else:
            raise ValueError(f"Invalid area chair type: {area_chair_type}. Choose from {','.join(const.AREA_CHAIR_TYPES)}.")
    elif phase == "ac_make_decisions":
        desc = ""

    if desc != "":
        bio += f"## Phong cách Chủ tọa (Your Biography)\n{desc}\n\n"

    return bio


def get_reviewer_player_config(
    reviewer_index: int,
    is_benign: bool = None,
    is_knowledgeable: bool = None,
    is_responsible: bool = None,
    global_settings: dict = None,
    backend_config: dict = None,
    **kwargs,
) -> AgentConfig:
    """Tạo cấu hình cho từng chuyên gia thẩm định trong hội đồng."""
    if global_settings is None:
        global_settings = {"provides_numeric_rating": ["reviewer", "ac"], "persons_aware_of_authors_identities": []}

    knows_authors = "reviewer" in global_settings.get("persons_aware_of_authors_identities", [])
    provides_numeric_rating = "reviewer" in global_settings.get("provides_numeric_rating", ["reviewer", "ac"])

    reviewer = {
        "name": f"Reviewer {reviewer_index}",
        "role_desc": get_reviewer_description(
            is_benign=is_benign,
            is_knowledgeable=is_knowledgeable,
            is_responsible=is_responsible,
            provides_numeric_rating=provides_numeric_rating,
            knows_authors=knows_authors,
            phase="reviewer_write_reviews",
            reviewer_index=reviewer_index
        ),
        "backend": dict(backend_config or PLAYER_BACKEND),
        "metadata": {
            "reviewer_index": reviewer_index,
            "is_benign": is_benign,
            "is_knowledgeable": is_knowledgeable,
            "is_responsible": is_responsible,
            "knows_authors": knows_authors,
        }
    }
    return AgentConfig(**reviewer)


def get_author_config(backend_config: dict = None) -> AgentConfig:
    """Tạo cấu hình cho Cơ quan chủ trì soạn thảo."""
    author = {
        "name": "Author",
        "role_desc": get_author_description(),
        "backend": dict(backend_config or PLAYER_BACKEND)
    }
    return AgentConfig(**author)


def get_document_extractor_config(**kwargs) -> AgentConfig:
    """Tạo cấu hình cho tác nhân bóc tách cấu trúc văn bản quy phạm pháp luật."""
    max_tokens = kwargs.pop("max_tokens", 2048)
    extractor = {
        "name": "Document Extractor",
        "role_desc": "Tác nhân trích xuất và phân tách các Chính sách, Chương, Điều, Khoản của văn bản quy phạm pháp luật.",
        "backend": {
            "backend_type": "dummy",
            "max_tokens": max_tokens,
        },
    }
    return AgentConfig(**extractor)


get_paper_extractor_config = get_document_extractor_config


def get_ac_config(backend_config: dict = None, **kwargs) -> AgentConfig:
    """Tạo cấu hình cho Chủ tịch Hội đồng thẩm định."""
    env_type = kwargs.pop("env_type", "ria_review")
    global_settings = kwargs.get("global_settings", {"persons_aware_of_authors_identities": []})

    if env_type in ["ria_review", "paper_review"]:
        phase = "ac_write_metareviews"
    elif env_type == "paper_decision":
        phase = "ac_make_decisions"
    else:
        phase = "ac_write_metareviews"

    kwargs["phase"] = phase
    kwargs["knows_authors"] = "ac" in global_settings.get("persons_aware_of_authors_identities", [])

    ac_backend = dict(backend_config or PLAYER_BACKEND)
    ac_backend["temperature"] = 0.1  # Giữ tính nhất quán cao khi lập Báo cáo RIA tổng hợp theo Mẫu số 01

    area_chair = {
        "name": "AC",
        "role_desc": get_ac_description(**kwargs),
        "backend": ac_backend,
        "env_type": env_type,
    }
    return AgentConfig(**area_chair)
