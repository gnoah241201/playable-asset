# playable-kit

Công cụ tách, thay và đóng gói lại asset cho playable PixiJS/webpack (bản build AppLovin/MRAID), đồng thời chuyển đổi sang Mintegral.
Tài liệu tích hợp đầy đủ cho AI agent nằm ở **[AGENTS.md](AGENTS.md)**.

> Chỉ dùng cho playable mà bạn sở hữu hoặc được phép chỉnh sửa. Không dùng để đổi link hoặc đổi skin creative của công ty khác.

```bash
pip install .                                   # cài lệnh playable-kit
playable-kit inspect  build.html              # xem file có được hỗ trợ không
playable-kit unpack   build.html -o ws        # tách asset ra ws/assets/
#   thay file trong ws/assets/ (giữ nguyên tên file), sửa link store trong ws/playable.json
playable-kit pack     ws                        # ra ws/dist/*_applovin.html + *_mintegral.zip
playable-kit pack     ws --quantize             # nén PNG xuống 256 màu nếu file nặng
playable-kit validate ws/dist/mygame_mintegral.zip
playable-kit smoke    ws/dist/mygame_mintegral.zip   # chạy thử trong Chrome headless (cần Node 22+ và Chrome)
playable-kit convert  build.html --to mintegral -o mygame_mintegral.zip   # chỉ chuyển đổi, không đổi asset
```
Thêm `--json` vào bất kỳ lệnh nào để nhận kết quả dạng JSON.
