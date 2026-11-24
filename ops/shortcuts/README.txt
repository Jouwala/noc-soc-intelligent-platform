OLBR 生产入口快捷 bat（ops\shortcuts\）
=====================================

这一组只是「薄转发」，本身不含启停逻辑：真正做事的是
ops\deploy\portal-ctl.ps1 与 ops\deploy\olbr-*.bat。改行为请改那里，
不要往这些 bat 里加逻辑（否则又出现第二套真相源）。

为什么 2026-11-19 重写
----------------------
本目录是 11-13「隔离副本」时代的入口，11-18/19 切生产（D:\02 接管
80/443/7038/7090/9267/9268）后没跟着改，留下了 4 个真问题：

1. 路径算错：%~dp0.. 是 ops\，不是仓库根。6/7 号原来读 ops\.env、
   ops\TMP、ops\nginx-1.28.0\logs —— 三个都不存在。
2. 0-status 背后的 status.ps1 按 .env 的隔离端口（8080/8443/7039/
   7091/9277）报告，而生产跑在 80/443/7038/7090/9267 —— 生产明明
   在跑，它永远显示「停」。
3. 1-start-all / 2-stop-all 走 my_portal_ctl.ps1，而它开头有一道 11-09 的
   「隔离副本」闸门：只要根目录还有 ISOLATED_COPY.flag，除 0-status 外的
   命令一律 [BLOCKED] 退出。所以这两个按钮在现在的生产树上是**坏的**
   （点了只打印 BLOCKED，起停都不生效），而不是能误停生产；2-stop-all
   这一侧本来也确实没有二次确认。
4. 5-nginx-check 直接跑 write_nginx_conf.ps1，而它会拿 .env 里的隔离
   端口重写线上 nginx.conf（线上是 80/443）—— 下次 reload / 看门狗
   重启即站点全挂。

现在每个入口做什么
------------------
0-status.bat                   只读：端口归属 / HTTP 健康 / 自愈任务状态
                               （portal-ctl.ps1 -Action status）
1-start-all.bat                启动：触发三个自愈任务，等端口就绪再报状态
                               （portal-ctl.ps1 -Action start）
2-stop-all.bat                 停止：转发 ops\deploy\olbr-stop.bat，
                               带 Y/N 二次确认（确认逻辑只写在那里）
5-nginx-check.bat              只读：显示线上 conf 的路径/时间/listen 指令
                               与 80/443 是否在听。**不再重建 conf**，
                               也不在 nginx 运行时跑 nginx -t（Windows 上
                               运行中的 master 占着 logs\access.log，
                               -t 一定报 access denied，即使语法没问题）
6-open-home.bat                打开 https://localhost/（生产固定 443；
                               不再从 .env 取 8443）
7-open-logs.bat                打开 <根>\TMP 与 <根>\nginx-1.28.0\logs
8-install-desktop-shortcuts.bat 调 desktop_shortcuts\make_desktop_shortcuts.ps1
                               -WriteDesktop，重建桌面 olbr_app 里的 13 个快捷方式

已删除：3-start-dryrun.bat / 4-stop-dryrun.bat —— 那是隔离副本的干跑
（start_all.ps1 -DryRun 会拿 .env 的 8080/8443 排计划，stop_all.ps1
-DryRun 只找 TMP\portable-*.pid）。生产上这两个入口没有含义，留着只会
给人「按了会发生什么」的错觉。真要在隔离副本里干跑，用根目录
start.bat -DryRun / stop.bat -DryRun。

.env 的现状（重要）
-------------------
根 .env 仍是**隔离端口**（NGINX_HTTP_PORT=8080 / NGINX_HTTPS_PORT=8443 /
PGPORT=55432 …）。切生产后它不是生产配置的真相源：生产端口写死在
ops\deploy\portal-ctl.ps1（80/443/7038/7090/9267/9268），nginx.conf 由
切生产那次生成，现在是 80/443。所以：**不要拿 .env 去生成生产 nginx.conf**。

约定
----
* 所有 .bat 必须是 ASCII + CRLF（.gitattributes 已按 *.bat eol=crlf 检出）。
  混入非 ASCII 会让 cmd 解析出错。
* 桌面快捷方式不要手工指到这些文件；跑 8-install-desktop-shortcuts.bat，
  生成器会按本机根目录算路径，换盘符/换目录后重跑一次即可。
* 生产语义的细则见 ops\deploy\README.md（portal-ctl.ps1、olbr-*.bat）。
