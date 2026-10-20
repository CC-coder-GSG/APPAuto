pipeline {
    agent any

    // 参数由 Jenkins 任务配置页维护，流水线只消费本次构建选择。
    options {
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
    }

    environment {
        PATH = "C:\\Windows\\System32;C:\\Windows\\System32\\WindowsPowerShell\\v1.0;${env.PATH}"
        OMNIQA_GIT_EXECUTABLE = "D:\\software\\Git\\bin\\git.exe"
        PYTHONUTF8 = "1"
        PYTHONIOENCODING = "utf-8"
        APK_DIR = "apk_in"
        ANDROID_HOME_DEFAULT = "D:\\android-sdk"
        TEST_REPO_URL = "git@github.com:CC-coder-GSG/SurveyMasterAutoTesting.git"
        DEFAULT_TEST_BRANCH = "main"
        OMNIQA_URL = "${params.OMNIQA_URL ?: ''}"
        OMNIQA_REPOSITORY_ID = "${params.OMNIQA_REPOSITORY_ID ?: ''}"
        OMNIQA_ENABLED = "${params.OMNIQA_ENABLED ?: false}"
        OMNIQA_DRY_RUN = "${params.OMNIQA_DRY_RUN ?: false}"
        OMNIQA_JENKINS_JOB = "SurveyMaster-RF-Test"
        TEST_DIR = "autotest"
        WECHAT_WEBHOOK = credentials('wecom_robot_webhook')
    }

    stages {
        stage('Show selection') {
            steps {
                echo "JOB_NAME=${params.JOB_NAME}"
                echo "PACKAGE=${params.PACKAGE}"
                echo "APK_PATH=${params.APK_PATH}"
                echo "TEST_ROBOTS=${params.TEST_ROBOTS}"
                echo "TEST_BRANCH=${params.TEST_BRANCH}"
                echo "DEVICE_ID=${params.DEVICE_ID}"
            }
        }

        stage('Prepare workspace') {
            steps {
                powershell(script: '''
                    $ErrorActionPreference = 'Stop'
                    if ([string]::IsNullOrWhiteSpace($env:WORKSPACE)) { throw 'WORKSPACE is empty' }
                    $workspaceRoot = [IO.Path]::GetFullPath($env:WORKSPACE).TrimEnd([IO.Path]::DirectorySeparatorChar)
                    $resultsDir = [IO.Path]::GetFullPath((Join-Path $workspaceRoot 'results'))
                    $apkDir = [IO.Path]::GetFullPath((Join-Path $workspaceRoot $env:APK_DIR))
                    foreach ($cleanupPath in @($resultsDir, $apkDir)) {
                        if ((Split-Path -Parent $cleanupPath) -ne $workspaceRoot) {
                            throw 'Cleanup target is outside the Jenkins workspace'
                        }
                    }

                    # 每次构建都清理旧参数和旧报告，避免误跑上一次选择的用例。
                    if (Test-Path -LiteralPath $resultsDir) {
                        Remove-Item -LiteralPath $resultsDir -Recurse -Force
                    }
                    New-Item -ItemType Directory -Path $resultsDir | Out-Null

                    if (Test-Path -LiteralPath $apkDir) {
                        Remove-Item -LiteralPath $apkDir -Recurse -Force
                    }
                ''')
            }
        }

        stage('Checkout AutoTests') {
            options { timeout(time: 10, unit: 'MINUTES') }
            steps {
                dir("${env.TEST_DIR}") {
                    deleteDir()
                    script {
                        def branch = params.TEST_BRANCH?.trim() ?: env.DEFAULT_TEST_BRANCH
                        echo "Checking out autotest branch: ${branch}"
                        retry(3) {
                            if (params.OMNIQA_ENABLED && params.TEST_REVISION?.trim()) {
                                def revision = params.TEST_REVISION.trim()
                                if (!(revision ==~ /(?:[0-9a-f]{40}|[0-9a-f]{64})/)) error 'Invalid TEST_REVISION'
                                checkout([$class: 'GitSCM', branches: [[name: revision]], userRemoteConfigs: [[url: env.TEST_REPO_URL]]])
                            } else {
                                git branch: branch, url: env.TEST_REPO_URL
                            }
                        }
                    }
                }
            }
        }

        stage('Generate local config') {
            steps {
                dir("${env.TEST_DIR}") {
                    script {
                        bat 'if not exist resources\\variables mkdir resources\\variables'
                        def yamlContent = """
APPIUM_SERVER: "${params.APPIUM_SERVER}"
PLATFORM_NAME: "${params.PLATFORM_NAME}"
PLATFORM_VERSION: "${params.PLATFORM_VERSION}"
AUTOMATION_NAME: "${params.AUTOMATION_NAME}"
DEVICE_NAME: "${params.DEVICE_ID}"
UDID: "${params.DEVICE_ID}"
APP_PACKAGE: "${params.APP_PACKAGE}"
APP_ACTIVITY: "${params.APP_ACTIVITY}"
NO_RESET: ${params.NO_RESET}
AUTO_GRANT_PERMISSIONS: ${params.AUTO_GRANT_PERMISSIONS}
NEW_COMMAND_TIMEOUT: ${params.NEW_COMMAND_TIMEOUT}
APP_WAIT_ACTIVITY: "${params.APP_WAIT_ACTIVITY}"
UNICODE_KEYBOARD: ${params.UNICODE_KEYBOARD}
RESET_KEYBOARD: true
IGNORE_HIDDEN_API_POLICY_ERROR: true
SKIP_DEVICE_INITIALIZATION: false
"""
                        writeFile(
                            file: 'resources/variables/env_test.yaml',
                            text: yamlContent,
                            encoding: 'UTF-8'
                        )
                        echo "Generated Appium config for ${params.DEVICE_ID}"
                    }
                }
            }
        }

        stage('Prepare Robot arguments') {
            steps {
                script {
                    if (params.OMNIQA_ENABLED) {
                        withCredentials([string(credentialsId: 'omniqa-surveymaster', variable: 'OMNIQA_KEY')]) {
                            dir("${env.TEST_DIR}") {
                                if (params.OMNIQA_CATALOG_ONLY) {
                                    bat '@echo off\r\n"C:\\Python314\\python.exe" ci\\omniqa_robot.py catalog --branch "%TEST_BRANCH%" --output "%WORKSPACE%\\results\\automation-catalog.json" --publish'
                                } else {
                                    bat '@echo off\r\n"C:\\Python314\\python.exe" ci\\omniqa_robot.py prepare --output-dir "%WORKSPACE%\\results"'
                                }
                            }
                        }
                        return
                    }
                    def rawSelection = params.TEST_ROBOTS?.toString()?.trim()
                    if (!rawSelection) {
                        error 'TEST_ROBOTS parameter is empty'
                    }

                    def selectedTests = rawSelection
                        .split(/[\r\n,]+/)
                        .collect { it.trim() }
                        .findAll { it }
                        .unique()

                    selectedTests.each { testPath ->
                        def normalized = testPath.replace('\\', '/')
                        if (!normalized.startsWith('tests/') ||
                            !normalized.endsWith('.robot') ||
                            normalized.contains('../')) {
                            error "Invalid TEST_ROBOTS path: ${testPath}"
                        }
                    }

                    // 通过 tests 根目录和 --suite 运行，确保父级 __init__.robot 的会话初始化生效。
                    def argumentLines = [
                        '--outputdir',
                        "${env.WORKSPACE}\\results"
                    ]
                    selectedTests.each { testPath ->
                        def normalized = testPath.replace('\\', '/')
                        def relativeSuite = normalized
                            .substring('tests/'.length(), normalized.length() - '.robot'.length())
                            .replace('/', '.')
                        argumentLines.add('--suite')
                        argumentLines.add("Tests.${relativeSuite}")
                    }
                    argumentLines.add('tests')

                    writeFile(
                        file: 'results/robot_args.txt',
                        text: argumentLines.join('\r\n') + '\r\n',
                        encoding: 'UTF-8'
                    )
                    writeFile(
                        file: 'results/selected_test_files.txt',
                        text: selectedTests.join('\r\n') + '\r\n',
                        encoding: 'UTF-8'
                    )
                    echo "Selected Robot files: ${selectedTests.join(', ')}"
                }
            }
        }

        stage('Acquire device') {
            when { expression { params.OMNIQA_ENABLED && !params.OMNIQA_DRY_RUN && !params.OMNIQA_CATALOG_ONLY } }
            steps {
                withCredentials([string(credentialsId: 'omniqa-surveymaster', variable: 'OMNIQA_KEY')]) {
                    dir("${env.TEST_DIR}") {
                        bat '@echo off\r\n"C:\\Python314\\python.exe" ci\\omniqa_robot.py device lock --output-dir "%WORKSPACE%\\results"'
                    }
                }
            }
        }

        stage('Copy selected APK') {
            when { expression { !(params.OMNIQA_ENABLED && (params.OMNIQA_DRY_RUN || params.OMNIQA_CATALOG_ONLY)) } }
            options { timeout(time: 5, unit: 'MINUTES') }
            steps {
                script {
                    if (!params.JOB_NAME?.trim()) {
                        error 'JOB_NAME parameter is empty'
                    }
                    if (!params.PACKAGE?.trim()) {
                        error 'PACKAGE parameter is empty'
                    }
                    if (!params.APK_PATH?.trim()) {
                        error 'APK_PATH parameter is empty'
                    }

                    def buildNo = params.PACKAGE.tokenize('|')[0].trim()
                    if (!(buildNo ==~ /\d+/)) {
                        error "Cannot parse build number from PACKAGE=${params.PACKAGE}"
                    }
                    echo "Copying APK from ${params.JOB_NAME} #${buildNo}: ${params.APK_PATH}"

                    copyArtifacts(
                        projectName: params.JOB_NAME,
                        selector: specific(buildNo),
                        filter: params.APK_PATH,
                        target: env.APK_DIR,
                        flatten: true
                    )
                }
            }
        }

        stage('Install APK') {
            when { expression { !(params.OMNIQA_ENABLED && (params.OMNIQA_DRY_RUN || params.OMNIQA_CATALOG_ONLY)) } }
            options { timeout(time: 10, unit: 'MINUTES') }
            steps {
                powershell """
                    \$ErrorActionPreference = 'Stop'
                    \$targetDevice = '${params.DEVICE_ID}'.Trim()
                    if ([string]::IsNullOrWhiteSpace(\$targetDevice)) {
                        throw 'DEVICE_ID parameter is empty'
                    }

                    \$apks = @(Get-ChildItem -Path '${env.APK_DIR}' -Filter *.apk -File)
                    if (\$apks.Count -ne 1) {
                        throw "Expected exactly one APK under ${env.APK_DIR}, found \$(\$apks.Count)"
                    }
                    \$apk = \$apks[0].FullName

                    \$androidHome = \$env:ANDROID_HOME
                    if ([string]::IsNullOrWhiteSpace(\$androidHome)) {
                        \$androidHome = '${env.ANDROID_HOME_DEFAULT}'
                    }
                    \$adb = Join-Path \$androidHome 'platform-tools\\adb.exe'
                    if (!(Test-Path \$adb)) {
                        throw "adb.exe not found: \$adb"
                    }

                    Write-Host "Checking device: \$targetDevice"
                    # 通过 cmd 丢弃 daemon 的输出句柄，避免新启动的 ADB 服务让 Jenkins PowerShell 步骤无法退出。
                    \$savedErrorAction = \$ErrorActionPreference
                    \$savedServerCookie = \$env:JENKINS_SERVER_COOKIE
                    \$savedNodeCookie = \$env:JENKINS_NODE_COOKIE
                    \$ErrorActionPreference = 'Continue'
                    \$env:JENKINS_SERVER_COOKIE = 'surveymaster-adb-daemon'
                    \$env:JENKINS_NODE_COOKIE = 'surveymaster-adb-daemon'
                    \$adbWorkingDir = Split-Path -Parent \$adb
                    Push-Location \$adbWorkingDir
                    \$killServerCommand = '""{0}" kill-server 1>nul 2>nul"' -f \$adb
                    & \$env:ComSpec /d /s /c \$killServerCommand
                    \$startServerCommand = '""{0}" start-server 1>nul 2>nul"' -f \$adb
                    & \$env:ComSpec /d /s /c \$startServerCommand
                    Pop-Location
                    \$env:JENKINS_SERVER_COOKIE = \$savedServerCookie
                    \$env:JENKINS_NODE_COOKIE = \$savedNodeCookie
                    \$ErrorActionPreference = \$savedErrorAction

                    \$online = \$false
                    for (\$attempt = 1; \$attempt -le 10; \$attempt++) {
                        \$ErrorActionPreference = 'Continue'
                        \$deviceState = & \$adb -s \$targetDevice get-state 2>\$null
                        \$ErrorActionPreference = \$savedErrorAction
                        if ("\$deviceState".Trim() -eq 'device') {
                            \$online = \$true
                            break
                        }
                        Start-Sleep -Seconds 1
                    }
                    if (!\$online) {
                        \$ErrorActionPreference = 'Continue'
                        \$deviceList = (& \$adb devices 2>\$null) -join '; '
                        \$ErrorActionPreference = \$savedErrorAction
                        throw "Device \$targetDevice is offline or unavailable. adb devices: \$deviceList"
                    }

                    Write-Host "Uninstalling ${params.APP_PACKAGE} from \$targetDevice"
                    & \$adb -s \$targetDevice uninstall '${params.APP_PACKAGE}' | Out-Host

                    Write-Host "Installing \$apk"
                    & \$adb -s \$targetDevice install -r -d \$apk | Out-Host
                    \$installExitCode = \$LASTEXITCODE
                    & \$env:ComSpec /d /s /c \$killServerCommand
                    if (\$installExitCode -ne 0) {
                        throw "APK installation failed with exit code \$installExitCode"
                    }
                """
            }
        }

        stage('Notify Start') {
            steps {
                powershell(script: '''
                    $ErrorActionPreference = 'Continue'
                    if ($env:OMNIQA_ENABLED -eq 'true') { exit 0 }
                    $notify = Join-Path $env:WORKSPACE "$env:TEST_DIR\\ci\\wecom_notify.ps1"
                    $plan = Join-Path $env:WORKSPACE 'results\\selected_test_files.txt'

                    if (-not (Test-Path -LiteralPath $notify)) {
                        Write-Host "[WARN] wecom_notify.ps1 not found: $notify"
                        exit 0
                    }

                    & $notify `
                        -Event start `
                        -Webhook "$env:WECHAT_WEBHOOK" `
                        -BuildUrl "$env:BUILD_URL" `
                        -JobName "$env:JOB_NAME" `
                        -BuildNumber "$env:BUILD_NUMBER" `
                        -TestFilesPath $plan `
                        -MaxFiles 30

                    exit 0
                ''')
            }
        }

        stage('Run AutoTests') {
            when { expression { !params.OMNIQA_CATALOG_ONLY || !params.OMNIQA_ENABLED } }
            options { timeout(time: 60, unit: 'MINUTES') }
            steps {
                dir("${env.TEST_DIR}") {
                    withEnv([
                        "DEVICE_ID=${params.DEVICE_ID}",
                        "APPIUM_PORT=${params.APPIUM_PORT}",
                        "ANDROID_HOME=${env.ANDROID_HOME_DEFAULT}"
                    ]) {
                        script {
                          if (params.OMNIQA_ENABLED && params.OMNIQA_DRY_RUN) {
                            bat '@echo off\r\n"C:\\Python314\\python.exe" -m robot -A "%WORKSPACE%\\results\\robot_args.txt"'
                        } else {
                            bat 'ci\\jenkins_verify.bat'
                          }
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            script {
                if (params.OMNIQA_ENABLED && fileExists('results/automation-plan.json')) {
                    withCredentials([string(credentialsId: 'omniqa-surveymaster', variable: 'OMNIQA_KEY')]) {
                        dir("${env.TEST_DIR}") {
                            try {
                                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                                    bat '@echo off\r\n"C:\\Python314\\python.exe" ci\\omniqa_robot.py report --output-dir "%WORKSPACE%\\results"'
                                }
                            } finally {
                                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                                    bat '@echo off\r\n"C:\\Python314\\python.exe" ci\\omniqa_robot.py device unlock --output-dir "%WORKSPACE%\\results"'
                                }
                            }
                        }
                    }
                }
            }
            archiveArtifacts(
                artifacts: 'results/**, appium.log, logcat_*.txt',
                allowEmptyArchive: true
            )
            script {
                if (fileExists('results/output.xml')) {
                    robot(
                        outputPath: 'results',
                        outputFileName: 'output.xml',
                        logFileName: 'log.html',
                        reportFileName: 'report.html',
                        passThreshold: 100.0,
                        unstableThreshold: 0.0
                    )
                }
            }
            powershell(script: '''
                $ErrorActionPreference = 'Continue'
                    if ($env:OMNIQA_ENABLED -eq 'true') { exit 0 }
                $notify = Join-Path $env:WORKSPACE "$env:TEST_DIR\\ci\\wecom_notify.ps1"

                if (-not (Test-Path -LiteralPath $notify)) {
                    Write-Host "[WARN] wecom_notify.ps1 not found: $notify"
                    exit 0
                }

                $outputXml = Join-Path $env:WORKSPACE 'results\\output.xml'
                if (Test-Path -LiteralPath $outputXml) {
                    Write-Host '[INFO] Send finish notify with output.xml'
                    & $notify `
                        -Event finish `
                        -Webhook "$env:WECHAT_WEBHOOK" `
                        -BuildUrl "$env:BUILD_URL" `
                        -OutputXml $outputXml `
                        -JobName "$env:JOB_NAME" `
                        -BuildNumber "$env:BUILD_NUMBER"
                } else {
                    Write-Host '[INFO] Send finish notify without output.xml (fallback)'
                    & $notify `
                        -Event finish `
                        -Webhook "$env:WECHAT_WEBHOOK" `
                        -BuildUrl "$env:BUILD_URL" `
                        -JobName "$env:JOB_NAME" `
                        -BuildNumber "$env:BUILD_NUMBER" `
                        -ExitCode 1
                }

                exit 0
            ''')
            powershell(script: '''
                $ErrorActionPreference = 'Stop'
                if ([string]::IsNullOrWhiteSpace($env:WORKSPACE)) { throw 'WORKSPACE is empty' }
                $workspaceRoot = [IO.Path]::GetFullPath($env:WORKSPACE).TrimEnd([IO.Path]::DirectorySeparatorChar)
                $apkDir = [IO.Path]::GetFullPath((Join-Path $workspaceRoot $env:APK_DIR))
                if ((Split-Path -Parent $apkDir) -ne $workspaceRoot) {
                    throw 'APK cleanup target is outside the Jenkins workspace'
                }
                if (Test-Path -LiteralPath $apkDir) {
                    Remove-Item -LiteralPath $apkDir -Recurse -Force
                }
            ''')
        }
    }
}
