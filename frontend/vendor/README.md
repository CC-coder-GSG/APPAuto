将以下第三方库文件放到当前目录，文件名必须保持一致：

1. `echarts.min.js`  
来源：`echarts@5.5.0/dist/echarts.min.js`

2. `xlsx.full.min.js`  
来源：`xlsx@0.18.5/dist/xlsx.full.min.js`

3. `html2pdf.bundle.min.js`  
来源：`html2pdf.js@0.10.1/dist/html2pdf.bundle.min.js`

页面已改为从以下站内路径加载：

- `/static/vendor/echarts.min.js`
- `/static/vendor/xlsx.full.min.js`
- `/static/vendor/html2pdf.bundle.min.js`

如果任一文件缺失，前端会显示友好提示并在控制台输出错误信息。
