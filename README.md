# Smart E-Mobility Hub

Bản MVP mở rộng cho bài tập Công nghệ phần mềm của nhóm A01-04. Ứng dụng dùng Python 3.10 trở lên và trình duyệt hiện đại.

## Chạy trên Windows

1. Giải nén ZIP và mở Terminal tại thư mục chứa app.py.
2. Chạy `py app.py --demo-af04`. Tùy chọn này nạp bộ sự kiện minh họa AF04 của ngày trước, có nhãn **Dữ liệu minh họa**. Để dùng bộ dữ liệu thường, chạy `py app.py`. Trên macOS hoặc Linux, dùng `python3 app.py`.
3. Mở [http://127.0.0.1:8080](http://127.0.0.1:8080). Cổng có thể đổi bằng `--port 8081`.
4. Đăng nhập bằng `student` hoặc `operator`. Hai tài khoản mẫu dùng mật khẩu `demo123`. Dùng hai tab nếu muốn chuyển vai khi demo.
5. Nhấn Ctrl+C trong Terminal để dừng máy chủ.

Máy chủ lắng nghe trên localhost. Dữ liệu nghiệp vụ, lịch sử và tùy chọn tài khoản nằm trong RAM của phiên chạy. Tùy chọn trước đăng nhập được lưu trong trình duyệt.

## Thử các chức năng

- **VI/EN và sáng/tối.** Dùng nút **English** ở góc trên để chuyển ngôn ngữ trên màn hình đang mở. Dữ liệu đã nhập trong biểu mẫu được giữ khi chuyển. Sau khi đăng nhập, mục **Giao diện** lưu cả ngôn ngữ và chế độ màu theo tài khoản.
- **AF04.** Với tùy chọn `--demo-af04`, đăng nhập Operator, mở **Giám sát**, chọn Hub Metro và ngày trước rồi bấm **Xem tổng hợp**. Bộ mẫu cho kết quả 3 chuyến hoàn tất, 2 từ chối thiếu tài nguyên, chờ sạc trung bình 6 phút và 1 sự cố mới. Tác vụ nền tổng hợp các ngày đã kết thúc theo giờ Việt Nam. Ngày chưa có báo cáo và ngày chỉ được quan sát một phần có nhãn riêng.
- **UC06, UC13 và UC15.** Student gửi nhu cầu sạc tại Hub Bách khoa. Operator chọn **Lập lịch sạc**, để phạm vi Bách khoa và chọn **Tính phương án**. Bản nháp xét thêm xe chung đang đỗ có pin dưới 20%, mục tiêu 80% và hạn mặc định sau 120 phút, theo cấu hình mẫu. **Áp dụng lịch** mới cam kết cổng sạc và chỗ đỗ. Sau khi áp dụng, dùng **Điều chỉnh** trên một lượt chưa bắt đầu. Chọn giờ sạc mới và hạn cần xe, tính lại rồi xác nhận áp dụng. Lượt cũ vẫn có hiệu lực trong lúc xem nháp và được ghi trong lịch sử khi thay thành công.
- **Hủy nhu cầu sạc.** Trong **Nhu cầu sạc → Nhu cầu của tôi**, Student chọn **Hủy nhu cầu sạc** ở yêu cầu của mình đang chờ hoặc đã lên lịch và chưa bắt đầu sạc. Yêu cầu chuyển sang **Đã hủy**. Nếu đã được xếp lịch, hệ thống giải phóng phần giữ cổng sạc và chỗ đỗ của lượt đó. Operator mở lại **Lập lịch sạc** để xem trạng thái mới và tính lại nếu đã tạo bản nháp trước lúc Student hủy.
- **UC14.** Mở **What-if**, chọn Hub và thời lượng 1–60 phút. Năm tham số bao gồm tăng lượt đến, Hub đầy chỗ đỗ, tăng nhu cầu sạc, hỏng cổng và tập trung quá nhiều xe. Đặt 0 cho tham số không sử dụng. Bảng kết quả so sánh hai kịch bản trên cùng bản chụp với bước một phút. Dùng **Chụp lại dữ liệu** khi muốn lấy phiên bản vận hành mới.
- **UC16.** Chạy `gateway_demo.py` trong terminal thứ hai. Trong giao diện Operator chọn **Giám sát → Làm mới** để kiểm tra sự kiện được áp dụng, bỏ qua hay cần đối soát. **Lập lịch sạc** hiển thị các phiên sạc thực tế. Nếu cần minh họa trạng thái dữ liệu cũ, khởi động máy chủ với `--external-iot` và gửi bản tin heartbeat theo Hub.

## Bản tin IoT mẫu

Cập nhật pin và gửi lại cùng mã để thử chống trùng.

```powershell
py gateway_demo.py battery EB-101 --value 42 --event-id test-pin-01
py gateway_demo.py battery EB-101 --value 65 --event-id test-pin-01
py gateway_demo.py heartbeat MET
```

Để thử xe vào và ra, Student đặt một chỗ đang mở cho xe cá nhân và lấy mã chỗ trên phiếu. Dùng mã thực tế thay cho `KTX-01`. Gửi bản tin khi đã đến thời điểm bắt đầu khung giữ chỗ.

```powershell
py gateway_demo.py parking_enter KTX-01 --vehicle PRIVATE-student
py gateway_demo.py parking_exit KTX-01 --vehicle PRIVATE-student
```

Để thử bắt đầu và kết thúc sạc, lấy mã lượt, cổng và xe ở **Lịch đã áp dụng**. Gửi bản tin bắt đầu trong khung giờ đã xác nhận. Thay các mã trong ví dụ bằng dữ liệu phiên hiện tại.

```powershell
py gateway_demo.py charging_started C-BKH-01 --vehicle EB-202 --slot-id CS-001 --battery 18
py gateway_demo.py charging_ended C-BKH-01 --vehicle EB-202 --slot-id CS-001 --battery 80
```

Để thử gián đoạn, gửi lỗi cổng sau khi bắt đầu và trước khi kết thúc sạc.

```powershell
py gateway_demo.py port_fault C-BKH-01
py gateway_demo.py port_status C-BKH-01 --value 0
```

Bản tin báo cổng bình thường được lưu làm quan sát. Operator xác nhận phục hồi qua hồ sơ sự cố. Có thể chọn nguồn thứ hai bằng `--source gateway-secondary` để thử quan sát mâu thuẫn.

## Kiểm tra

Chạy `py verify_extended.py` để kiểm tra thiết lập tài khoản, lập lịch, điều chỉnh và hủy nhu cầu sạc, sự kiện IoT, năm nhóm What-if, AF04 và giữ xe đồng thời. Mô phỏng sử dụng dữ liệu và mô hình tính toán trong bản demo. Các bài đo nghiệm thu NFR của đặc tả cần môi trường đo riêng.
