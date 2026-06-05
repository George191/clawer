@echo off
:: 本地Google Patents数据加载脚本（Windows）
:: 用法:
::   scripts\load_patents.bat              - 生成模拟数据并测试加载流程
::   scripts\load_patents.bat real         - 加载真实数据

cd /d "%~dp0.."

if "%1"=="real" (
    echo 加载真实数据...
    python -m app.tools.load_local_patents ^
        --data-dir "D:\BaiduNetdiskDownload\029 -美国专利全量数据库（1790-2024）" ^
        --template-name google_patent_local ^
        --data-type patent
) else (
    echo 生成并加载模拟数据（测试流程）...
    
    :: 1. 生成模拟数据
    python -m app.tools.generate_mock_patents ^
        --count 100 ^
        --output mock_patents.json ^
        --format json
    
    :: 2. 创建临时目录并移动测试数据
    mkdir temp_test_data 2>nul
    move /Y mock_patents.json temp_test_data\
    
    :: 3. 加载测试数据
    python -m app.tools.load_local_patents ^
        --data-dir temp_test_data ^
        --template-name google_patent_test ^
        --data-type patent
)
