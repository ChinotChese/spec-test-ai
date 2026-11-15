# Exporters Spec — SpecTestAI

| File | Bước trong [app.py](../app.py) | Trạng thái |
|---|---|---|
| [markdown.py](markdown.py) | 7. Xuất SRS ra Markdown | ✔ Đã triển khai |

Liên quan:

- [services/srs_validator.py](../services/srs_validator.py) kiểm tra SRS và dò nguồn gốc trước khi xuất ([services/spec.md](../services/spec.md) mục 4).
- [schemas/srs.py](../schemas/srs.py) là dữ liệu đầu vào.

---

## 1. `markdown.py`

Xuất một SRS (`schemas.srs.SRS` hoặc dict/JSON tương ứng) ra file `.md`, kèm trích dẫn tới passage nguồn và bảng truy vết cho từng FR/NFR.

### 1.1 Quy trình

1. **Validate trước:** gọi `srs_validator.validate(srs, requirements)`. Nếu có lỗi thì raise `SrsValidationError` và **không ghi file nào**.
2. **Dựng toàn bộ nội dung trong bộ nhớ** từ kết quả của validator. Exporter dùng chính bảng truy vết mà validator đã kiểm tra, không tự tra lại DB.
3. **Ghi file nguyên tử:** ghi vào `<tên>.md.tmp`, rồi đổi tên thành `<tên>.md`. File được ghi bằng UTF-8, xuống dòng kiểu `\n`.

### 1.2 Vị trí và tên file

| | Mặc định |
|---|---|
| Thư mục | `config.OUTPUT_DIR` = `src/output` (đổi bằng `SPECTEST_OUTPUT_DIR` hoặc tham số `out_dir`) |
| Tên file | `SRS_<dự án>_v<version>_<ngày tạo>.md`, ví dụ `SRS_AI-Powered-Customer-Support-Platform_v1-0_2026-11-16.md`. Tên dự án được bỏ dấu và chỉ giữ chữ, số, gạch nối. Đổi bằng tham số `filename` |

Xuất lại cùng dự án, cùng version, cùng ngày sẽ **ghi đè** file cũ.

`src/output` **chưa** được `.gitignore` bỏ qua, trong khi file SRS chứa nguyên văn tài liệu nguồn (email khách hàng). Cân nhắc trước khi commit.

### 1.3 Cấu trúc file

Theo [templates/IEEE_830_SRS_Template.md](../templates/IEEE_830_SRS_Template.md). Số và tên mục lấy từ **mục lục của chính SRS**, nên tiêu đề luôn khớp `table_of_contents`.

| Phần | Nội dung |
|---|---|
| Tiêu đề | Tên dự án, version, người soạn, tổ chức, ngày tạo |
| Table of Contents | Liên kết tới từng mục và các phụ lục |
| Revision History | Bảng |
| 1–2, 4 | Văn xuôi kèm trích dẫn. Mục `unresolved` / `not_applicable` hiển thị dạng `> **Unresolved:** <lý do>` |
| 1.5 References | Bảng tài liệu nguồn |
| 3.N | Mô tả feature kèm trích dẫn |
| 5.N.1–5.N.3 | Mô tả, priority; các cặp stimulus/response (liên kết tới FR); từng FR: `**REQ-n.m:** statement [S…]`, priority, sequence liên quan, **Origin: registry RQ-…** (liên kết tới bảng truy vết) |
| 6.N | Từng NFR: statement, priority, verification, acceptance measure, origin; nhóm không có NFR thì ghi Unresolved |
| Appendix A. Traceability Matrix | Mỗi evidence của mỗi FR/NFR một dòng: **SRS id → registry id → passage → tài liệu, trang → quote đã lưu** |
| Appendix B. Sources | Mỗi passage được trích một mục: số `[S…]`, passage id, tên tài liệu, trang, **nguyên văn passage** |
| Appendix C. Validation Notes | Số requirement đã truy nguồn, số passage được trích, và các warning của validator |

### 1.4 Trích dẫn

- Mỗi passage được đánh số `S1`, `S2`… theo **thứ tự xuất hiện lần đầu**. Trích dẫn là liên kết `[S3](#src-3)` tới Appendix B.
- Anchor được viết bằng HTML (`<a id="src-3"></a>`, `<a id="req-4-1"></a>`, `<a id="sec-5-4"></a>`), không dùng cú pháp footnote, để liên kết hoạt động trong mọi trình xem Markdown.

### 1.5 Escape

Mọi text động (lấy từ model, registry hay tài liệu nguồn) đều được escape:

| Ký tự | Xử lý | Lý do |
|---|---|---|
| `<` `>` `&` | `&lt;` `&gt;` `&amp;` | Placeholder `<Project Name>` hay địa chỉ `<a@b.de>` sẽ bị hiểu là thẻ HTML và biến mất |
| `\` `` ` `` `*` `_` `[` `]` | Thêm `\` | Tránh in nghiêng, in đậm hoặc tạo liên kết ngoài ý muốn |
| `\|` và xuống dòng trong ô bảng | `\|`, dấu cách | Tránh vỡ bảng. Ngoài bảng, `\|` để nguyên |

### 1.6 API

```python
from exporters import markdown

path = markdown.export(srs, requirements)            # app.py
path = markdown.export(srs, requirements, requirement_map=report.requirement_map,
                       out_dir="...", filename="...", repo=repo)
text = markdown.render(validation_report)            # chỉ dựng nội dung, không ghi file
```

| Hàm | Mô tả |
|---|---|
| `export(srs, requirements=None, *, requirement_map=None, out_dir=None, filename=None, repo=None) -> Path` | Validate, dựng, ghi; trả đường dẫn file |
| `render(report: SrsValidationReport) -> str` | Nội dung Markdown từ kết quả của validator |
| `default_filename(srs) -> str` | Tên file mặc định |

### 1.7 Dòng lệnh

Chạy từ `src/`. Lệnh dùng `requirement_registry.get_approved()` làm danh sách requirement:

```bash
uv run python -m agents.srs_generator --project "..." --org ... --author "..." --out output/srs.json
uv run python -m exporters.markdown output/srs.json            # -> src/output/SRS_....md
uv run python -m exporters.markdown srs.json --out-dir DIR --name SRS.md
```

Thành công thì in đường dẫn file. Lỗi validation thì in từng lỗi và thoát với exit code 1, không ghi file.

### 1.8 Kết quả trên dữ liệu thật

SRS của lần sinh thật thứ 2 ([agents/spec.md](../agents/spec.md) mục 2.8) được xuất bằng **bản sao** của DB:

- **File:** `src/output/SRS_AI-Powered-Customer-Support-Platform_v1-0_2026-11-16.md`, khoảng 51 KB, 940 dòng.
- **Kiểm tra bằng cách đọc lại file:**
  - 27/27 FR có dòng trong Appendix A;
  - 53 trích dẫn đều trỏ tới một mục trong Appendix B;
  - 35/35 quote nằm trong passage gốc trong DB;
  - 53/53 mục nguồn chép đúng nguyên văn passage;
  - tiêu đề khớp đúng mục lục.
- **Warning:** 5 cái (3 mục văn xuôi `unresolved`/`not_applicable`, cộng 6.1 và 6.2 không có NFR). Không có lỗi.

### 1.9 Test

- **Sáu kiểu SRS bị sửa đổi đều bị từ chối, không ghi file nào:**
  - đổi statement;
  - đổi priority;
  - tráo `source_ids`;
  - thêm FR;
  - requirement bị reject sau khi sinh;
  - narrative trích passage không tồn tại.

  Bảng ánh xạ sai cũng bị từ chối.
- **Đọc lại file:** mọi FR/NFR có dòng truy vết, mọi trích dẫn trỏ tới nguồn, mọi quote nằm trong passage, mục nguồn chép đúng nguyên văn, tiêu đề khớp mục lục.
- **Escape:** placeholder, `\|` và `<>` trong tên file, ký tự in nghiêng; mục `unresolved` hiển thị đúng; không còn file `.tmp`.
