# Internal File Manager

หน้า FastAPI มี File Manager สำหรับไฟล์ที่แอปจัดการอยู่ทางซ้ายของ Assessment Workspace เดิม โดยไม่เปลี่ยนขั้นตอนหรืออัลกอริทึม Phase 03–07

## Managed roots

- Generated outputs: `data/output` (ค่าปริยาย)
- Source PDFs: `data/input/uploads` (อ่าน/ดาวน์โหลดได้ แต่ File Manager ไม่ลบ)
- Original filename index: `data/input/uploads/.file-manager-metadata.json` (ระบบสร้างอัตโนมัติและไม่อยู่ในรายการที่ลบได้)
- ชนิด Generated Output ที่อนุญาต: `.json`, `.jsonl`, `.pdf`, `.docx`
- ชนิด Source ที่อนุญาต: `.pdf`

เปลี่ยน Generated Output roots ได้ด้วย `IOP_FILE_MANAGER_OUTPUT_ROOTS` โดยคั่นหลาย path ด้วย `;` บน Windows หรือ `:` บน Linux ควรกำหนดเฉพาะโฟลเดอร์ผลลัพธ์ที่แอปเป็นเจ้าของเท่านั้น

Document ID มาจาก metadata `document_id` ใน JSON ขนาดที่ตรวจได้ หรือจาก prefix ชื่อไฟล์รูปแบบ `DOC_<เลขฐานสิบหก 12 ตัว>` แบบ exact match เท่านั้น หาก metadata กับชื่อขัดกันจะอยู่ใน “ไม่จัดหมวดหมู่” และไม่ถูกลบแบบทั้งเอกสาร Source PDF จะจับคู่ด้วย `source_file` ใน Phase 03 metadata ไม่ใช้การค้นหา substring

## Access control

Repository นี้ไม่มี SSO/session/reverse-proxy authentication ที่ตรวจยืนยันได้ จึงใช้ Shared Access Token สำหรับ destructive APIs และปิดการลบเป็นค่าเริ่มต้น

ตั้งค่าก่อนเปิด Uvicorn:

```powershell
$env:IOP_FILE_MANAGER_ACCESS_TOKEN = "ใส่ค่า random ที่ยาวและคาดเดายาก"
$env:IOP_FILE_MANAGER_ID_SECRET = "ใส่ค่า random อีกค่าหนึ่งเพื่อให้ file ID คงที่"
python -m uvicorn webapp.main:app --reload
```

ผู้ใช้กรอก Shared Access Token ที่ Sidebar หนึ่งครั้งต่อ browser session แล้ว browser ส่งเป็น `Authorization: Bearer ...` เฉพาะคำสั่งตรวจสิทธิ์/ลบ Token ไม่ถูกเขียนลงไฟล์หรือ audit log และไม่ได้ใช้ cookie จึงไม่เกิด ambient cookie credential/CSRF แต่ deployment ต้องใช้ HTTPS เพื่อป้องกัน token ระหว่างทาง

ถ้าไม่ตั้ง `IOP_FILE_MANAGER_ACCESS_TOKEN` ปุ่มลบจะถูกปิดและทุก destructive API ตอบ `503 deletion_disabled` ฝั่งเซิร์ฟเวอร์ การซ่อนปุ่มไม่ใช่กลไก authorization

## APIs

- `GET /api/files` — รายการและพื้นที่รวมของ managed files
- `POST /api/files/access/check` — ตรวจ Shared Access Token
- `GET /api/files/{file_id}/download` — ดาวน์โหลดด้วย opaque ID ที่ตรวจ root/type/symlink ซ้ำ
- `DELETE /api/files/{file_id}` — ลบ output หนึ่งไฟล์; ต้อง Bearer token และ body `{"confirmed": true}`
- `POST /api/documents/{document_id}/outputs/delete/preview` — รายการ output ที่จะลบและ confirmation token อายุสั้น
- `POST /api/documents/{document_id}/outputs/delete` — ลบตาม preview; ต้อง Bearer token, `confirmed` และ `confirmation_token`

Bulk delete ไม่รวม Source PDF และจะตรวจรายการ/ขนาด/mtime/Document ID/สถานะ active job ซ้ำก่อนลบ หากไฟล์เปลี่ยนหลัง Preview จะไม่เริ่มลบ ผลลัพธ์รายงานรายการที่สำเร็จและล้มเหลวตามจริง การลบ Phase 04 มีคำเตือนว่าต้องสร้าง chunks/embeddings ใหม่ก่อนรัน Phase 05

## Security notes

- API ไม่รับ filesystem path จาก client
- Opaque file ID เป็น locator ไม่ใช่ authorization; ทุก operation ตรวจ allow-listed root และ extension ซ้ำ
- ปฏิเสธ symlink, path escape, source deletion, unsafe extension และ active job
- Preview token ใช้ครั้งเดียว อายุ 5 นาที และผูกกับ Document ID กับ fingerprint ของไฟล์
- Audit log บันทึก operation, shared actor, opaque file ID, Document ID, outcome และ error category โดยไม่บันทึก token/path/เนื้อหาเอกสาร
- Job state อยู่ในหน่วยความจำตามระบบเดิม ดังนั้น active-job protection ครอบคลุม process Uvicorn ปัจจุบัน ไม่ใช่งานจาก process/เครื่องอื่น ควรรัน worker เดียวจนกว่าจะมี shared job store

## Tests

ชุดทดสอบใช้ `tmp_path` เท่านั้น:

```powershell
python -m pytest tests/test_file_manager.py tests/test_file_manager_api.py -q --basetemp .pytest_tmp_file_manager
```
