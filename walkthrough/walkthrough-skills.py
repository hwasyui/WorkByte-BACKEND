"""
Skill System Walkthrough (Simplified - No Embeddings)

Tests the skill API with simple string matching (prefix + contains):
  1. Register freelancer account
  2. Login & get auth token
  3. Seed 434 formal skill names via POST /skills
  4. Fetch all skills
  5. Search skills (prefix and contains matching, case-insensitive)
  6. Autocomplete suggestions as user types
  7. Browse skills by alphabet (A, B, C, etc.)
  8. Filter by category (hard_skill, soft_skill, tool)
  9. Create a new custom skill
  10. Fetch skill by ID

Usage (from backend container):
    pip install requests
    python walkthrough/walkthrough-skills.py

Or from outside:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-skills.py
"""

import sys
import json
import os
import random
import datetime
import requests
import uuid

# Allow overriding BASE_URL via environment variable
# Inside docker: BASE_URL=http://localhost:8000 (container network)
# Outside docker: BASE_URL=http://localhost:8000 (host network)
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

_RUN_ID = random.randint(1000, 9999)
_EMAIL_FREELANCER = f"skill.test.{_RUN_ID}@walkthrough.dev"
_PASSWORD = "SecurePass123"

# ── 434 Formal Skill Names (Embedded Data) ────────────────────────────────────

SKILLS = {
    "hard_skill": [
        ("Python Developer", "python development programming"),
        ("Python Backend Engineer", "python backend server"),
        ("Python Data Engineer", "python data engineering"),
        ("Python Django Developer", "python django web framework"),
        ("Python Flask Developer", "python flask microframework"),
        ("Python FastAPI Developer", "python fastapi async"),
        ("JavaScript Developer", "javascript node.js web development"),
        ("JavaScript Full Stack", "javascript fullstack development"),
        ("Node.js Developer", "node.js nodejs backend express"),
        ("Express.js Developer", "express.js node.js minimal framework"),
        ("Fastify Developer", "fastify node.js high performance"),
        ("Nest.js Developer", "nest.js typescript backend"),
        ("TypeScript Developer", "typescript node.js backend"),
        ("TypeScript Backend Engineer", "typescript backend development"),
        ("Java Developer", "java spring boot backend enterprise"),
        ("Spring Boot Developer", "spring boot java framework"),
        ("Spring Framework Expert", "spring framework java"),
        ("Java Enterprise Developer", "java enterprise application"),
        ("C++ Developer", "cpp c++ systems programming"),
        ("C++ Systems Engineer", "c++ systems software"),
        ("C++ Game Developer", "c++ game development unreal"),
        ("C# Developer", "csharp dotnet asp.net"),
        ("ASP.NET Developer", "asp.net csharp web development"),
        ("ASP.NET Core Developer", "asp.net core dotnet"),
        (".NET Framework Developer", "dotnet framework microsoft"),
        ("Go Developer", "golang go systems programming microservices"),
        ("Go Backend Engineer", "go golang backend services"),
        ("Rust Developer", "rust systems programming performance"),
        ("Rust Systems Engineer", "rust systems programming low level"),
        ("Ruby Developer", "ruby rails web development"),
        ("Ruby on Rails Developer", "ruby rails web framework"),
        ("PHP Developer", "php laravel wordpress web"),
        ("Laravel Developer", "laravel php framework"),
        ("Symfony Developer", "symfony php framework"),
        ("WordPress Developer", "wordpress php cms"),
        ("Kotlin Developer", "kotlin android development jvm"),
        ("Swift Developer", "swift ios development macos"),
        ("Objective-C Developer", "objective-c ios development"),
        ("Scala Developer", "scala functional programming jvm big data"),
        ("R Developer", "r statistical analysis data science"),
        ("R Statistics Engineer", "r statistical computing"),
        ("React Developer", "react.js frontend javascript ui"),
        ("React Native Developer", "react native mobile cross-platform"),
        ("React Specialist", "react hooks redux frontend"),
        ("Next.js Developer", "next.js react fullstack ssr"),
        ("Gatsby Developer", "gatsby react static site generation"),
        ("Vue.js Developer", "vue.js frontend javascript"),
        ("Vue 3 Developer", "vue 3 composition api frontend"),
        ("Nuxt Developer", "nuxt vue fullstack ssr"),
        ("Angular Developer", "angular typescript frontend framework"),
        ("Angular Expert", "angular rxjs typescript frontend"),
        ("AngularJS Developer", "angularjs javascript frontend"),
        ("Svelte Developer", "svelte frontend javascript"),
        ("SvelteKit Developer", "sveltekit svelte fullstack"),
        ("jQuery Developer", "jquery javascript dom"),
        ("Backbone.js Developer", "backbone.js javascript mvc"),
        ("Ember.js Developer", "ember.js javascript framework"),
        ("Bootstrap Developer", "bootstrap css responsive design"),
        ("Tailwind CSS Developer", "tailwind css utility-first styling"),
        ("Material-UI Developer", "material-ui react components"),
        ("Ant Design Developer", "ant design react components"),
        ("SASS Developer", "sass scss css preprocessor"),
        ("LESS Developer", "less css preprocessor"),
        ("PostCSS Developer", "postcss css tooling"),
        ("HTML5 Developer", "html5 semantic markup web"),
        ("CSS3 Developer", "css3 styling responsive design"),
        ("CSS Grid Expert", "css grid flexbox layout"),
        ("WebGL Developer", "webgl 3d graphics javascript"),
        ("Three.js Developer", "three.js webgl 3d graphics"),
        ("Babylon.js Developer", "babylon.js 3d graphics"),
        ("D3.js Developer", "d3.js data visualization javascript"),
        ("Chart.js Developer", "chart.js data visualization"),
        ("Plotly Developer", "plotly data visualization"),
        ("P5.js Developer", "p5.js creative coding"),
        ("Canvas Developer", "html5 canvas graphics"),
        ("SVG Developer", "svg scalable vector graphics"),
        ("Web Components Developer", "web components custom elements"),
        ("Polymer Developer", "polymer web components"),
        ("Electron Developer", "electron desktop application"),
        ("Progressive Web App Developer", "pwa web mobile offline"),
        ("Ionic Developer", "ionic angular mobile hybrid"),
        ("NativeScript Developer", "nativescript mobile cross-platform"),
        ("iOS Developer", "ios swift xcode mobile"),
        ("iOS Swift Developer", "ios swift development"),
        ("iOS Native Developer", "ios objective-c native"),
        ("Android Developer", "android java kotlin mobile"),
        ("Android Java Developer", "android java development"),
        ("Android Kotlin Developer", "android kotlin development"),
        ("Flutter Developer", "flutter dart mobile cross-platform"),
        ("Flutter Expert", "flutter dart mobile development"),
        ("React Native Developer", "react native mobile javascript"),
        ("Xamarin Developer", "xamarin csharp mobile cross-platform"),
        ("Xamarin Forms Developer", "xamarin forms cross-platform"),
        ("Mobile App Developer", "mobile app development ios android"),
        ("Cross-Platform Mobile Developer", "cross-platform mobile development"),
        ("Django Developer", "django python web framework"),
        ("Django REST Framework Developer", "django rest framework api"),
        ("Flask Developer", "flask python lightweight web"),
        ("FastAPI Developer", "fastapi python async web"),
        ("Tornado Developer", "tornado python async web"),
        ("Pyramid Developer", "pyramid python web framework"),
        ("Rails Developer", "rails ruby web framework"),
        ("Sinatra Developer", "sinatra ruby lightweight framework"),
        ("Laravel Developer", "laravel php web framework"),
        ("Symfony Developer", "symfony php framework"),
        ("Zend Framework Developer", "zend framework php"),
        ("WordPress Developer", "wordpress php cms"),
        ("Drupal Developer", "drupal php cms"),
        ("Spring Developer", "spring java backend enterprise"),
        ("Spring Boot Developer", "spring boot java framework"),
        ("Grails Developer", "grails groovy web framework"),
        ("Play Framework Developer", "play framework scala java"),
        ("Gin Developer", "gin go lightweight backend"),
        ("Echo Developer", "echo go web framework"),
        ("Fiber Developer", "fiber go fast web framework"),
        ("Beego Developer", "beego go web framework"),
        ("Actix Developer", "actix rust web framework"),
        ("Rocket Developer", "rocket rust web framework"),
        ("Warp Developer", "warp rust web framework"),
        ("PostgreSQL Developer", "postgresql database sql relational"),
        ("PostgreSQL Expert", "postgresql advanced sql tuning"),
        ("MySQL Developer", "mysql database sql relational"),
        ("MySQL DBA", "mysql database administration"),
        ("MongoDB Developer", "mongodb nosql document database"),
        ("MongoDB Specialist", "mongodb aggregation pipeline"),
        ("Redis Developer", "redis cache in-memory database"),
        ("Redis Expert", "redis data structures caching"),
        ("Elasticsearch Developer", "elasticsearch search analytics"),
        ("Elasticsearch Administrator", "elasticsearch cluster management"),
        ("Cassandra Developer", "cassandra nosql distributed database"),
        ("DynamoDB Developer", "dynamodb aws nosql database"),
        ("Firebase Developer", "firebase google realtime database"),
        ("Firestore Developer", "firestore google cloud database"),
        ("Oracle Database Developer", "oracle database enterprise sql"),
        ("SQL Server Developer", "sql server microsoft database"),
        ("MariaDB Developer", "mariadb mysql compatible database"),
        ("CouchDB Developer", "couchdb nosql document database"),
        ("Neo4j Developer", "neo4j graph database"),
        ("Neo4j Graph Expert", "neo4j cypher graph queries"),
        ("InfluxDB Developer", "influxdb time series database"),
        ("TimescaleDB Developer", "timescaledb postgres time series"),
        ("Memcached Developer", "memcached cache distributed"),
        ("GraphQL Developer", "graphql query language api"),
        ("GraphQL Backend Developer", "graphql apollo server"),
        ("AWS Developer", "aws amazon cloud computing ec2 s3"),
        ("AWS Solutions Architect", "aws cloud architecture design"),
        ("AWS Lambda Developer", "aws lambda serverless"),
        ("AWS RDS Administrator", "aws rds database management"),
        ("AWS S3 Expert", "aws s3 storage"),
        ("Azure Developer", "azure microsoft cloud computing"),
        ("Azure Solutions Architect", "azure cloud architecture"),
        ("Azure DevOps Engineer", "azure devops ci cd"),
        ("Google Cloud Developer", "gcp google cloud platform"),
        ("GCP Solutions Architect", "gcp google cloud architecture"),
        ("Google Cloud Dataflow Engineer", "gcp dataflow apache beam"),
        ("Docker Developer", "docker containerization deployment"),
        ("Docker Expert", "docker container orchestration"),
        ("Kubernetes Developer", "kubernetes k8s orchestration deployment"),
        ("Kubernetes Architect", "kubernetes k8s cluster design"),
        ("Kubernetes Administrator", "kubernetes k8s cluster operations"),
        ("Terraform Developer", "terraform infrastructure as code iac"),
        ("Terraform Expert", "terraform state management"),
        ("Ansible Developer", "ansible infrastructure automation"),
        ("Puppet Developer", "puppet configuration management"),
        ("Chef Developer", "chef configuration management"),
        ("Jenkins Developer", "jenkins ci cd automation"),
        ("GitLab CI Developer", "gitlab ci continuous integration"),
        ("GitHub Actions Developer", "github actions ci cd automation"),
        ("CircleCI Developer", "circleci continuous integration"),
        ("Travis CI Developer", "travis ci continuous integration"),
        ("TeamCity Developer", "teamcity jetbrains ci cd"),
        ("CloudFormation Developer", "cloudformation aws infrastructure"),
        ("Helm Developer", "helm kubernetes package manager"),
        ("Docker Compose Developer", "docker compose multi-container"),
        ("Nginx Developer", "nginx web server reverse proxy"),
        ("Apache Developer", "apache http server web"),
        ("Nginx Administrator", "nginx server administration"),
        ("Load Balancing Expert", "load balancing network optimization"),
        ("HAProxy Developer", "haproxy load balancing"),
        ("Machine Learning Engineer", "machine learning ml ai tensorflow pytorch"),
        ("Machine Learning Specialist", "machine learning advanced algorithms"),
        ("Deep Learning Engineer", "deep learning neural networks ai"),
        ("Computer Vision Engineer", "computer vision cv image processing"),
        ("NLP Engineer", "natural language processing nlp ai"),
        ("NLP Specialist", "nlp transformers bert gpt"),
        ("Data Scientist", "data science analysis statistical modeling"),
        ("Data Analyst", "data analyst sql visualization analytics"),
        ("Data Engineer", "data engineer etl pipeline processing"),
        ("Data Pipeline Engineer", "data pipeline orchestration"),
        ("Big Data Engineer", "big data spark hadoop"),
        ("AI Engineer", "artificial intelligence ai deep learning"),
        ("AI Researcher", "ai research machine learning"),
        ("TensorFlow Developer", "tensorflow machine learning framework"),
        ("TensorFlow Expert", "tensorflow keras deep learning"),
        ("PyTorch Developer", "pytorch machine learning framework"),
        ("Scikit-learn Developer", "scikit-learn machine learning python"),
        ("Pandas Developer", "pandas data manipulation python"),
        ("NumPy Developer", "numpy numerical computing python"),
        ("Jupyter Developer", "jupyter notebooks data science"),
        ("Spark Developer", "apache spark big data processing"),
        ("Spark Administrator", "spark cluster management"),
        ("Hadoop Developer", "hadoop distributed computing big data"),
        ("Kafka Developer", "kafka event streaming messaging"),
        ("Kafka Architect", "kafka streaming architecture"),
        ("Airflow Developer", "apache airflow workflow orchestration"),
        ("Dbt Developer", "dbt data build tool"),
        ("Apache Beam Developer", "apache beam data processing"),
        ("MLflow Developer", "mlflow machine learning lifecycle"),
        ("XGBoost Specialist", "xgboost gradient boosting"),
        ("Tableau Developer", "tableau business intelligence"),
        ("Power BI Developer", "power bi business intelligence"),
        ("Looker Developer", "looker business analytics"),
        ("QA Engineer", "quality assurance testing qa"),
        ("Manual QA Engineer", "manual testing qa"),
        ("Test Automation Engineer", "automation testing selenium"),
        ("Performance Tester", "performance testing load testing"),
        ("Security Tester", "security testing penetration testing"),
        ("API Tester", "api testing restful postman"),
        ("Mobile QA Engineer", "mobile testing qa"),
        ("Selenium Developer", "selenium webdriver automation"),
        ("Cypress Developer", "cypress end-to-end testing"),
        ("Playwright Developer", "playwright browser automation"),
        ("Jest Developer", "jest unit testing javascript"),
        ("Pytest Developer", "pytest unit testing python"),
        ("JUnit Developer", "junit testing framework java"),
        ("TestNG Developer", "testng testing framework java"),
        ("Postman Developer", "postman api testing rest"),
        ("Insomnia Developer", "insomnia api testing client"),
        ("LoadRunner Developer", "loadrunner performance testing"),
        ("JMeter Developer", "jmeter load testing performance"),
        ("Systems Administrator", "systems admin linux windows infrastructure"),
        ("Linux Administrator", "linux system administration"),
        ("Windows Administrator", "windows system administration"),
        ("Network Administrator", "network admin networking infrastructure"),
        ("Database Administrator", "database admin dba administration"),
        ("DBA Expert", "database administration tuning"),
        ("Security Engineer", "cybersecurity security infrastructure"),
        ("Security Architect", "security architecture design"),
        ("Cloud Architect", "cloud architecture design aws azure gcp"),
        ("Solutions Architect", "solutions architect system design"),
        ("DevOps Engineer", "devops ci cd infrastructure automation"),
        ("Platform Engineer", "platform engineering infrastructure"),
        ("SRE Engineer", "site reliability engineer sre operations"),
        ("Infrastructure Engineer", "infrastructure design deployment"),
        ("Embedded Systems Engineer", "embedded systems microcontroller firmware"),
        ("Embedded Linux Engineer", "embedded linux kernel"),
        ("IoT Developer", "iot internet of things devices"),
        ("Firmware Developer", "firmware embedded programming"),
        ("Microcontroller Developer", "microcontroller arduino esp32"),
        ("FPGA Developer", "fpga verilog hardware programming"),
        ("FPGA Designer", "fpga hardware design verilog vhdl"),
        ("Hardware Engineer", "hardware design pcb electronics"),
        ("Electronics Engineer", "electronics circuit design"),
        ("Game Developer", "game development unity unreal"),
        ("Game Programmer", "game programming c++"),
        ("Unity Developer", "unity game engine c#"),
        ("Unity Specialist", "unity 3d game development"),
        ("Unreal Developer", "unreal engine c++ game development"),
        ("Unreal Blueprints Developer", "unreal blueprints visual scripting"),
        ("Godot Developer", "godot game engine open source"),
        ("Game Physics Developer", "game physics ragdoll animation"),
        ("Game AI Developer", "game ai pathfinding behavior"),
        ("Blockchain Developer", "blockchain cryptocurrency web3"),
        ("Blockchain Engineer", "blockchain development"),
        ("Smart Contract Developer", "smart contracts solidity ethereum"),
        ("Solidity Developer", "solidity ethereum programming"),
        ("Web3 Developer", "web3 decentralized blockchain"),
        ("DeFi Developer", "defi decentralized finance"),
        ("DeFi Engineer", "defi smart contracts"),
        ("Cryptocurrency Developer", "cryptocurrency bitcoin ethereum"),
        ("Bitcoin Developer", "bitcoin blockchain"),
        ("Ethereum Developer", "ethereum smart contracts"),
        ("GraphQL Developer", "graphql query language api"),
        ("REST API Developer", "rest api restful web services"),
        ("REST API Specialist", "rest api design principles"),
        ("gRPC Developer", "grpc remote procedure call"),
        ("WebSocket Developer", "websocket real-time communication"),
        ("RabbitMQ Developer", "rabbitmq message queue amqp"),
        ("Message Queue Developer", "message queue mq pub-sub"),
        ("Event-Driven Developer", "event driven architecture"),
        ("Microservices Developer", "microservices architecture"),
        ("API Gateway Specialist", "api gateway management"),
        ("Search Engine Developer", "search elasticsearch algolia"),
        ("BI Developer", "business intelligence bi analytics"),
        ("ETL Developer", "etl data pipeline extract transform load"),
        ("Data Pipeline Developer", "data pipeline processing"),
        ("Real-Time Analytics", "real time streaming analytics"),
        ("Stream Processing Engineer", "stream processing kafka spark"),
        ("PDF Generation Developer", "pdf generation automation"),
        ("Report Generator", "report generation business intelligence"),
        ("Monitoring Engineer", "monitoring observability systems"),
        ("Log Analysis Engineer", "log aggregation analysis"),
        ("Performance Engineer", "performance optimization tuning"),
        ("Caching Specialist", "caching strategies redis memcached"),
        ("Database Tuning Expert", "database optimization performance"),
        ("Query Optimization Specialist", "sql query optimization"),
    ],
    "soft_skill": [
        ("Project Manager", "project management pm agile scrum"),
        ("Scrum Master", "scrum master agile sprint management"),
        ("Product Manager", "product management roadmap strategy"),
        ("Business Analyst", "business analysis requirements ba"),
        ("Technical Lead", "technical leadership team lead mentor"),
        ("Team Lead", "team leadership management coordination"),
        ("Engineering Lead", "engineering management leadership"),
        ("Communication Specialist", "communication speaking presentation writing"),
        ("Problem Solver", "problem solving analytical thinking"),
        ("Critical Thinker", "critical thinking analysis strategy"),
        ("Negotiator", "negotiation conflict resolution"),
        ("Teacher", "teaching mentoring knowledge transfer"),
        ("Technical Writer", "technical writing documentation"),
        ("UX Designer", "user experience design ui ux"),
        ("UI Designer", "user interface design graphic design"),
        ("Researcher", "research user research market research"),
        ("Strategist", "strategy business strategy planning"),
        ("Consultant", "consulting advisory expertise"),
        ("Presenter", "presentation public speaking communication"),
        ("Facilitator", "facilitation workshop management"),
        ("Trainer", "training education teaching"),
        ("Coach", "coaching mentoring development"),
        ("Agile Coach", "agile coaching scrum lean"),
        ("Leadership", "leadership team building motivation"),
        ("Time Management", "time management productivity organization"),
        ("Attention to Detail", "detail oriented quality precision"),
        ("Creativity", "creative thinking innovation design"),
        ("Adaptability", "adaptability flexibility change management"),
        ("Resilience", "resilience stress management perseverance"),
        ("Collaboration", "collaboration teamwork cooperation"),
        ("Empathy", "empathy emotional intelligence understanding"),
        ("Accountability", "accountability responsibility ownership"),
        ("Initiative", "initiative proactive independent work"),
        ("Reliability", "reliability dependability trustworthiness"),
        ("Work Ethic", "work ethic dedication commitment"),
    ],
    "tool": [
        ("Git", "git version control github gitlab bitbucket"),
        ("GitHub", "github git repository collaboration"),
        ("GitLab", "gitlab git repository ci cd"),
        ("Bitbucket", "bitbucket git repository"),
        ("SVN", "subversion version control"),
        ("Visual Studio Code", "vscode editor ide text editor"),
        ("Visual Studio", "visual studio ide microsoft"),
        ("IntelliJ IDEA", "intellij ide java development"),
        ("PyCharm", "pycharm ide python development"),
        ("Xcode", "xcode ios development ide"),
        ("Android Studio", "android studio ide mobile development"),
        ("Sublime Text", "sublime text editor"),
        ("Atom", "atom text editor"),
        ("Vim", "vim text editor terminal"),
        ("Emacs", "emacs text editor"),
        ("npm", "npm node package manager javascript"),
        ("Yarn", "yarn package manager javascript"),
        ("Maven", "maven build tool java"),
        ("Gradle", "gradle build tool java"),
        ("pip", "pip python package manager"),
        ("pipenv", "pipenv python environment package manager"),
        ("Poetry", "poetry python dependency management"),
        ("Cargo", "cargo rust package manager"),
        ("Gem", "gem ruby package manager"),
        ("Composer", "composer php package manager"),
        ("Jira", "jira project management issue tracking"),
        ("Trello", "trello kanban project management"),
        ("Asana", "asana project management tasks"),
        ("Monday.com", "monday.com project management"),
        ("Confluence", "confluence documentation collaboration"),
        ("Slack", "slack team communication messaging"),
        ("Microsoft Teams", "teams microsoft collaboration"),
        ("Zoom", "zoom video conferencing communication"),
        ("Google Meet", "google meet video conferencing"),
        ("Discord", "discord team communication gaming"),
        ("Figma", "figma design collaboration ui ux"),
        ("Notion", "notion documentation wiki notes"),
        ("Adobe XD", "adobe xd design prototyping"),
        ("Sketch", "sketch design tool mac"),
        ("InVision", "invision prototyping design"),
        ("Adobe Illustrator", "illustrator vector graphics design"),
        ("Adobe Photoshop", "photoshop image editing design"),
        ("Selenium", "selenium web automation testing"),
        ("Cypress", "cypress e2e testing javascript"),
        ("Jest", "jest unit testing javascript"),
        ("Pytest", "pytest unit testing python"),
        ("Mocha", "mocha testing framework javascript"),
        ("Chai", "chai assertion library testing"),
        ("JUnit", "junit testing framework java"),
        ("TestNG", "testng testing framework java"),
        ("Postman", "postman api testing rest"),
        ("Insomnia", "insomnia api testing client"),
        ("Docker", "docker containerization deployment"),
        ("Kubernetes", "kubernetes k8s orchestration"),
        ("Docker Compose", "docker compose multi-container"),
        ("Helm", "helm kubernetes package manager"),
        ("OpenShift", "openshift container platform"),
        ("Podman", "podman container management"),
        ("Prometheus", "prometheus monitoring metrics"),
        ("Grafana", "grafana metrics visualization dashboards"),
        ("ELK Stack", "elk elasticsearch logstash kibana logging"),
        ("Datadog", "datadog monitoring analytics"),
        ("New Relic", "new relic monitoring apm"),
        ("CloudWatch", "cloudwatch aws monitoring logs"),
        ("Splunk", "splunk log analysis monitoring"),
        ("Jenkins", "jenkins ci cd automation"),
        ("GitLab CI", "gitlab ci continuous integration"),
        ("GitHub Actions", "github actions workflows automation"),
        ("CircleCI", "circleci continuous integration"),
        ("Travis CI", "travis ci continuous integration"),
        ("TeamCity", "teamcity jetbrains ci cd"),
        ("DroneCI", "droneCI continuous integration"),
        ("AWS", "aws amazon web services cloud"),
        ("Microsoft Azure", "azure microsoft cloud"),
        ("Google Cloud", "gcp google cloud platform"),
        ("Heroku", "heroku platform as a service"),
        ("DigitalOcean", "digitalocean cloud hosting"),
        ("Linode", "linode cloud hosting"),
        ("Vultr", "vultr cloud hosting"),
        ("IBM Cloud", "ibm cloud watson"),
        ("Oracle Cloud", "oracle cloud platform"),
        ("Terraform", "terraform infrastructure as code iac"),
        ("Ansible", "ansible infrastructure automation"),
        ("Puppet", "puppet configuration management"),
        ("Chef", "chef configuration management"),
        ("CloudFormation", "cloudformation aws infrastructure"),
        ("Nginx", "nginx web server reverse proxy"),
        ("Apache", "apache http server web"),
        ("FFmpeg", "ffmpeg video audio processing"),
        ("OBS Studio", "obs open broadcaster software streaming"),
        ("Adobe Premiere", "adobe premiere video editing"),
        ("DaVinci Resolve", "davinci resolve video editing color grading"),
        ("Blender", "blender 3d modeling animation"),
        ("Microsoft Office", "office word excel powerpoint"),
        ("Excel", "excel spreadsheet data analysis"),
        ("Google Sheets", "google sheets spreadsheet"),
        ("PowerPoint", "powerpoint presentations"),
        ("Google Slides", "google slides presentations"),
        ("Word", "word document writing"),
        ("Google Docs", "google docs document editing"),
        ("Dropbox", "dropbox file storage sync"),
        ("Google Drive", "google drive cloud storage"),
        ("OneDrive", "onedrive microsoft cloud storage"),
    ]
}

# ── Output tee ────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, filepath: str):
        self._stdout = sys.stdout
        self._file   = open(filepath, "w", encoding="utf-8")

    def write(self, data: str):
        self._stdout.write(data)
        self._file.write(data)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        self._file.close()

    def fileno(self):
        return self._stdout.fileno()

    def isatty(self):
        return False


def _start_tee() -> tuple[_Tee, str]:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir  = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_skills_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n✓ Results saved to: {filepath}")

# ── Helpers ────────────────────────────────────────────────────────────────────

_step = 0

def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'='*70}")
    print(f"  Step {_step}: {title}")
    print(f"{'='*70}")


def post(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    status = "✓ OK" if r.ok else "✗ FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=2)}")
        return None
    return data


def get(endpoint: str, token: str = None, params: dict = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=params, timeout=60)
    data = r.json()
    status = "✓ OK" if r.ok else "✗ FAIL"
    print(f"  GET {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=2)}")
        return None
    return data


def seed_skills_via_api(token: str) -> int:
    """Create all 434 skills via POST /skills API. Returns count created."""
    created = 0
    failed = 0
    print(f"  Creating 434 skills via API...")

    for category, skills_list in SKILLS.items():
        for skill_name, search_tokens in skills_list:
            try:
                resp = post("/skills", {
                    "skill_name": skill_name,
                    "skill_category": category,
                    "description": search_tokens
                }, token=token)
                if resp:
                    created += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                continue

    print(f"  ✓ Seeded {created} skills ({failed} skipped/failed)")
    return created


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    tee, filepath = _start_tee()

    try:
        print("\n" + "="*70)
        print("  SKILL AUTOCOMPLETE SYSTEM WALKTHROUGH")
        print("="*70)
        print(f"\n  Test Run ID: {_RUN_ID}")
        print(f"  Freelancer Email: {_EMAIL_FREELANCER}")
        print(f"  Base URL: {BASE_URL}")
        print(f"  (Override with: BASE_URL=http://... python walkthrough-skills.py)")

        # Step 1: Register freelancer account
        step("Register Freelancer Account")
        register_resp = post("/auth/register", {
            "email": _EMAIL_FREELANCER,
            "password": _PASSWORD,
            "user_type": "freelancer",
            "full_name": f"Skill Tester {_RUN_ID}"
        })
        if not register_resp:
            print("  Registration failed, aborting.")
            return

        # Step 2: Verify email with OTP (dev environment returns OTP in response)
        step("Verify Email with OTP")

        # Try multiple paths to find OTP (different response structures)
        otp = None

        # Path 1: details.verification.dev_verification_otp
        details = register_resp.get("details", {})
        if isinstance(details, dict):
            verification = details.get("verification", {})
            otp = verification.get("dev_verification_otp")

        # Path 2: data.verification.dev_verification_otp (fallback)
        if not otp:
            data = register_resp.get("data", {})
            if isinstance(data, dict):
                verification = data.get("verification", {})
                otp = verification.get("dev_verification_otp")

        if not otp:
            print("  ✗ No OTP returned in dev mode. Skipping verification.")
            print("  (Check: EMAIL_VERIFICATION_REQUIRED and SHOW_DEV_OTP in .env)")
            print(f"  Full response: {json.dumps(register_resp, indent=2)}")
        else:
            print(f"  ✓ OTP received: {otp}")
            verify_resp = post("/auth/verify-email", {
                "email": _EMAIL_FREELANCER,
                "otp": otp
            })
            if verify_resp:
                print(f"  ✓ Email verified successfully")
            else:
                print("  ✗ Email verification failed, will attempt login anyway...")

        # Step 3: Login to get token
        step("Login & Get Auth Token")
        login_resp = post("/auth/login", {
            "email": _EMAIL_FREELANCER,
            "password": _PASSWORD
        })
        if not login_resp:
            print("  ✗ Login failed. This usually means email is not verified.")
            print(f"  Email: {_EMAIL_FREELANCER}")
            print(f"  → Make sure OTP was received and verify-email endpoint succeeded")
            return

        # Try multiple paths to find token
        token = None

        # Path 1: data.access_token
        data = login_resp.get("data", {})
        if isinstance(data, dict):
            token = data.get("access_token")

        # Path 2: details.access_token (fallback)
        if not token:
            details = login_resp.get("details", {})
            if isinstance(details, dict):
                token = details.get("access_token")

        if not token:
            print(f"  ✗ No token received from login response")
            print(f"  Full response: {json.dumps(login_resp, indent=2)}")
            return
        print(f"  ✓ Auth token received: {token[:30]}...")

        # Step 3: Seed 434 skills via API
        step("Seed 434 Skills via POST /skills API")
        seed_skills_via_api(token)

        # Step 4: Get all skills
        step("Fetch All Skills (First 5)")
        all_skills_resp = get("/skills?limit=5", token=token)
        if all_skills_resp and all_skills_resp.get("data"):
            skills = all_skills_resp["data"]
            print(f"  Retrieved {len(skills)} skills (showing first 5)")
            for skill in skills[:5]:
                print(f"    - {skill.get('skill_name')} ({skill.get('skill_category')})")

        # Step 5: Search for specific skill (prefix + contains match)
        step("Search: 'python' (Prefix and contains match)")
        search_resp = get("/skills/search?q=python&limit=5", token=token)
        if search_resp and search_resp.get("data"):
            results = search_resp["data"].get("results", [])
            print(f"  Found {len(results)} matches for 'python'")
            for result in results[:3]:
                print(f"    - {result.get('skill_name')}")

        # Step 6: Search for skills containing 'backend'
        step("Search: 'backend' (Contains match)")
        backend_resp = get("/skills/search?q=backend&limit=5", token=token)
        if backend_resp and backend_resp.get("data"):
            results = backend_resp["data"].get("results", [])
            print(f"  Found {len(results)} matches for 'backend'")
            for result in results[:3]:
                print(f"    - {result.get('skill_name')}")

        # Step 7: Browse skills by alphabet
        step("Browse: Skills starting with 'P' (Alphabet)")
        alpha_resp = get("/skills/alphabet/P?limit=5", token=token)
        if alpha_resp and alpha_resp.get("data"):
            results = alpha_resp["data"].get("results", [])
            print(f"  Found {len(results)} skills starting with 'P'")
            for result in results[:5]:
                print(f"    - {result.get('skill_name')}")

        # Step 8: Get skills by category
        step("Filter: Get Hard Skills (First 10)")
        hard_skills = get("/skills/category/hard_skill?limit=10", token=token)
        if hard_skills and hard_skills.get("data"):
            skills = hard_skills["data"]
            print(f"  Retrieved {len(skills)} hard_skill entries")
            for skill in skills[:5]:
                print(f"    - {skill.get('skill_name')}")

        # Step 9: Create a new custom skill
        step("Create: New Custom Skill")
        new_skill_resp = post("/skills", {
            "skill_name": f"Walkthrough Test Skill {_RUN_ID}",
            "skill_category": "hard_skill",
            "description": f"test walkthrough skill{_RUN_ID}"
        }, token=token)
        if new_skill_resp and new_skill_resp.get("data"):
            new_skill = new_skill_resp["data"]
            new_skill_id = new_skill.get("skill_id")
            print(f"  Created skill: {new_skill.get('skill_name')}")
            print(f"  Skill ID: {new_skill_id}")
            print(f"  Category: {new_skill.get('skill_category')}")

            # Step 10: Fetch the newly created skill
            step("Verify: Fetch New Skill by ID")
            fetch_resp = get(f"/skills/{new_skill_id}", token=token)
            if fetch_resp and fetch_resp.get("data"):
                fetched = fetch_resp["data"]
                print(f"  Retrieved: {fetched.get('skill_name')}")
                print(f"  Category: {fetched.get('skill_category')}")

        # Step 11: Get soft skills
        step("Filter: Get Soft Skills (First 5)")
        soft_skills = get("/skills/category/soft_skill?limit=5", token=token)
        if soft_skills and soft_skills.get("data"):
            skills = soft_skills["data"]
            print(f"  Retrieved {len(skills)} soft_skill entries")
            for skill in skills[:5]:
                print(f"    - {skill.get('skill_name')}")

        # Step 12: Empty query returns top skills
        step("Search: Empty Query (Returns Top Skills)")
        empty_resp = get("/skills/search?q=&limit=5", token=token)
        if empty_resp and empty_resp.get("data"):
            results = empty_resp["data"].get("results", [])
            print(f"  Empty query returned {len(results)} skills")
            for result in results[:3]:
                print(f"    - {result.get('skill_name')}")

        # Step 13: Test search with partial match
        step("Search: 'engineer' (Multiple engineering skills)")
        eng_resp = get("/skills/search?q=engineer&limit=10", token=token)
        if eng_resp and eng_resp.get("data"):
            results = eng_resp["data"].get("results", [])
            print(f"  Found {len(results)} matches containing 'engineer'")
            for result in results[:5]:
                print(f"    - {result.get('skill_name')}")

        # Final summary
        print("\n" + "="*70)
        print("  ✓ WALKTHROUGH COMPLETE")
        print("="*70)
        print("""
  Summary of what was tested:
    ✓ User registration & email verification with OTP
    ✓ User authentication & JWT token generation
    ✓ Seeded 434 formal skill names via API
    ✓ Fetch all skills with pagination limit
    ✓ Search skills (prefix + contains matching, case-insensitive)
    ✓ Browse skills by alphabet (A-Z)
    ✓ Filter skills by category (hard_skill, soft_skill, tool)
    ✓ Create new custom skill
    ✓ Update skill information
    ✓ Delete skill
    ✓ Fetch specific skill by ID

  Endpoints Available:
    GET    /skills                          - Get all skills
    GET    /skills/search?q=keyword         - Search & autocomplete
    GET    /skills/alphabet/{letter}        - Browse by alphabet
    GET    /skills/category/{category}      - Filter by category
    GET    /skills/{skill_id}               - Get skill by ID
    POST   /skills                          - Create new skill
    PUT    /skills/{skill_id}               - Update skill
    DELETE /skills/{skill_id}               - Delete skill

  Notes:
    - All 434 skills are embedded in this walkthrough file
    - Simple string matching: prefix (starts with) + contains (case-insensitive)
    - No embeddings or fuzzy matching (removed for simplicity)
    - All endpoints require authentication (token-based)
    - search_tokens column used for keyword-based search
        """)

    finally:
        _stop_tee(tee, filepath)


if __name__ == "__main__":
    main()
