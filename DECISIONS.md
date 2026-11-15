# Các quyết định thiết kế — SpecTestAI

Tóm tắt các lựa chọn chính khi xây dựng hệ thống, theo thứ tự từ dữ liệu đến đầu ra: **chọn gì, vì sao, và đã bỏ phương án nào**. Chi tiết nằm trong `spec.md` của từng thư mục và trong [README.md](README.md).

**Nguyên tắc chung:** AI dùng để *đọc* và *viết*, còn code và con người quyết định điều gì là requirement. Mọi requirement phải truy được về nguyên văn tài liệu nguồn.

---

## 1. Dữ liệu

| Quyết định | Lý do | Phương án đã bỏ |
|---|---|---|
| **SQLite**, file cục bộ `src/data/spectest.db` | Ứng dụng một người dùng, chạy local; không cần server | DB server |
| Quan hệ nhiều-nhiều dùng **bảng nối** (`requirement_evidence`, `srs_run_requirements`) | Database tự kiểm tra khóa ngoại, chặn được cả dữ liệu ghi thẳng bằng SQL; truy vấn ngược "passage → requirement" nhanh | Cột JSON (`evidence_json`, `requirement_ids_json`) như trong ERD ban đầu: chỉ kiểm tra được bằng code Python |
| Field FR/NFR là **7 cột riêng** (`priority`, `feature`, `nfr_category`…), có ràng buộc CHECK đặt tên | Database tự kiểm tra "FR phải có feature, NFR phải có đủ 3 field"; lọc và sắp xếp dễ | Một cột `details_json`: gọn nhưng database không kiểm tra được gì |
| Lưu document + passage, và lưu lô requirement, **trong một transaction** | Lỗi không để lại dữ liệu dở | Ghi từng dòng |
| Lệnh `init_db` kiểm tra DB có lệch schema không | Chưa có migration; DB cũ cần được phát hiện trước khi ghi lỗi | Để lỗi xảy ra lúc chạy |

## 2. Schema

| Quyết định | Lý do |
|---|---|
| Schema SRS (`schemas/srs.py`) bám sát `templates/standard_srs.yaml` (IEEE 830), có validator kiểm tra mục lục, đánh số, mục 3 ↔ 5 | Đầu ra phải đúng chuẩn và kiểm tra được bằng máy, không chỉ "trông giống" SRS |
| Mục chưa có thông tin phải ghi `unresolved` kèm lý do | Thà nói thật là thiếu còn hơn để model bịa |
| `RequirementDraft` (output của AI) tách khỏi `Requirement` (bản đã lưu) | Model không được tự đặt id hay status |

## 3. Đọc tài liệu

| Quyết định | Lý do | Phương án đã bỏ |
|---|---|---|
| Dùng **Gemini** để tách đoạn văn cho cả PDF/DOCX/TXT/MD; PDF gửi nguyên file | Model hiểu bố cục trang (cột, header/footer, gạch nối) tốt hơn quy tắc thủ công | Tự tách bằng quy tắc |
| **Kiểm tra nguyên văn** từng đoạn so với text gốc; đoạn không khớp bị loại | Passage là bằng chứng; model lite có thể diễn đạt lại hoặc bịa | Tin output của model |
| Id cố định từ sha256 của file (`DOC-…-P0001`) | Chạy lại cho cùng id; file đã ingest không bị gọi model lần nữa | uuid ngẫu nhiên |

## 4. Gọi model

| Quyết định | Lý do |
|---|---|
| **Mỗi bước một model** (`GEMINI_MODEL_LOADER` = flash-lite; `_EXTRACTOR` và `_GENERATOR` = flash) | Tách đoạn là việc đơn giản, nên dùng model rẻ; trích và viết cần model mạnh hơn |
| Một module dùng chung `llm_clients/gemini_client.py`; prompt tách riêng trong `src/prompts/` | Một chỗ cho retry, timeout và xử lý lỗi; prompt sửa được mà không đụng code |
| Retry 408/5xx và 429 theo phút; **không retry khi hết quota ngày** | Retry khi hết quota chỉ tốn thời gian |
| Structured output bằng **Pydantic**; output nào cũng được validate lại | Không parse văn bản tự do |
| Lỗi của một tài liệu được bọc lại và **bỏ qua tài liệu đó**; chỉ báo lỗi khi mọi tài liệu đều hỏng | Một file hỏng không chặn cả lô, nhưng hết quota cũng không bị hiểu nhầm thành "không có requirement" |

## 5. Trích requirement

| Quyết định | Lý do |
|---|---|
| Tái sử dụng vai trò và tiêu chí của prompt cũ (`AI-Requirement-Engineering-System/prompts`), nhưng **bỏ chỉ dẫn "tự sinh NFR từ bối cảnh"** | Giữ phần tốt, bỏ phần cho phép bịa |
| Mỗi requirement phải có **evidence ID + quote**; trích id lạ hay id của tài liệu khác thì bị loại, không tự sửa | Truy vết là mục đích chính của hệ thống |
| Schema gửi model có hai biến thể FR / NFR, mỗi biến thể có field bắt buộc riêng | Model không thể trả FR thiếu `feature` |
| **Đề xuất của một bên mà bên kia chưa phản đối vẫn được trích, priority `could`** | Không mất thông tin; người review quyết định giữ hay bỏ |
| Mô tả hiện trạng hay công cụ đang dùng là bối cảnh, không phải requirement | Sửa lỗi model suy diễn thấy khi chạy thật |

## 6. Review và approve

| Quyết định | Lý do | Phương án đã bỏ |
|---|---|---|
| **Evidence hợp lệ** = có passage, có quote, quote nằm trong chính passage đó; **một item sai thì loại cả requirement** | Không cắt bớt evidence để statement khẳng định nhiều hơn những gì còn lại chứng minh | Giữ requirement với các item hợp lệ; chấp nhận evidence không có quote |
| Một định nghĩa duy nhất (`database/evidence_rules.py`) dùng ở mọi tầng | Kiểm tra lúc lưu và lúc approve không bao giờ lệch nhau | Mỗi tầng tự kiểm tra một kiểu |
| Id `RQ-0001…`, tuần tự, cấp trong cùng transaction | Dễ nói trong lúc review ("approve RQ-0012"); khác hẳn id `REQ-1.1` của SRS | Id theo loại (FR/NFR): sai khi đổi loại |
| **Không có đường tự động approve**; chặn approve **ngay ở repository**, ở mọi đường dẫn tới `approved` | Gọi thẳng repository cũng không lách được; con người luôn là người quyết định | Chỉ chặn ở tầng service |

## 7. Sinh và xuất SRS

| Quyết định | Lý do |
|---|---|
| **Code dựng mục 3, 5, 6** (requirement chép nguyên văn, tự đánh số); model chỉ viết văn xuôi và các cặp stimulus/response | Bảo đảm "không có requirement mới" bằng cấu trúc, không bằng kiểm tra hậu kiểm |
| Mục 1.2 (quy ước của tài liệu) và thông tin dự án do code ghi | Model từng ghi sai cách đánh số; tên dự án không để model đoán |
| Validator **dò lại nguồn gốc** từng FR/NFR theo statement nguyên văn | Schema SRS không có chỗ cho id `RQ-`; vẫn phải chứng minh được nguồn gốc |
| Placeholder và mục `unresolved` chỉ là **warning** | Không chặn đường chạy mặc định; vẫn hiện rõ trong file xuất |
| Markdown có trích dẫn `[S…]`, bảng truy vết và nguyên văn nguồn; chỉ ghi file khi validate xong | Người đọc tự kiểm chứng được từng requirement |

## 8. Quyền riêng tư

| Quyết định | Lý do |
|---|---|
| `src/input`, `src/data`, `src/output`, `src/.env` không đưa vào git | Chứa tài liệu và trích dẫn nguyên văn của khách hàng |
| **Viết lại lịch sử git** để xóa tài liệu khách hàng và thông tin định danh trong docs, trước khi có remote | Xóa trong commit mới không đủ, vì commit cũ vẫn bị đẩy lên |
| Ví dụ trong tài liệu chỉ dùng tên giả | Tránh rò rỉ qua tài liệu |

---

## Còn để ngỏ

- **Chưa có khái niệm "dự án"** trong DB. Một SRS dùng mọi requirement đã approve.
- **Chống trùng chỉ bắt câu giống hệt nhau.** Requirement diễn đạt khác vẫn cần người review loại bỏ.
- **Chưa lưu SRS vào `srs_runs`**; chưa có test tự động trong repo.
