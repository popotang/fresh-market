import { useState, type CSSProperties, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Divider,
  Input,
  Segmented,
  Space,
  Tag,
  Typography,
  message,
  theme,
} from "antd";
import { CodeOutlined } from "@ant-design/icons";
import {
  YTAIButton,
  YTAIInput,
  YTRichEditor,
  YtAddressPicker,
  YtCodeEditor,
  YtUpload,
  YtUserSelect,
  type YTUserSelectCode,
  type YtAddressPickerOption,
  type YtUploadFile,
} from "@lovrabet/components";
import { LOVRABET_APP_CODE } from "@/api/api";
import { apiFetch, runtimeFetch } from "@/api/fetch";
import DemoPageContainer from "@/components/demo-page-container/DemoPageContainer";
import styles from "./index.module.css";

const { Paragraph, Text, Title } = Typography;

const WIDE_CARD_CLASS_NAME = `${styles.demoCard} ${styles.wideCard}`;
const APP_CODE =
  LOVRABET_APP_CODE && LOVRABET_APP_CODE !== "NOT-SET"
    ? LOVRABET_APP_CODE
    : undefined;

// 仅用于本地展示上传效果，不会将文件写入远端服务
const mockUpload = async (file: File) => URL.createObjectURL(file);

const addressOptions: YtAddressPickerOption[] = [
  {
    label: "Zhejiang",
    value: "zhejiang",
    children: [
      { label: "Hangzhou", value: "hangzhou" },
      { label: "Ningbo", value: "ningbo" },
    ],
  },
  {
    label: "Guangdong",
    value: "guangdong",
    children: [
      { label: "Guangzhou", value: "guangzhou" },
      { label: "Shenzhen", value: "shenzhen" },
    ],
  },
];

const snippets = {
  // 请求实例在应用根部注入一次（src/main.tsx），组件自动继承，
  // 下面各组件用法无需再逐个传 apiFetch / runtimeFetch
  provider: `import { YtConfigProvider } from "@lovrabet/components";
import { apiFetch, runtimeFetch } from "@/api/fetch";

<YtConfigProvider apiFetch={apiFetch} runtimeFetch={runtimeFetch}>
  <App />
</YtConfigProvider>`,
  userSelect: `import { YtUserSelect } from "@lovrabet/components";
import { LOVRABET_APP_CODE } from "@/api/api";

const appCode = LOVRABET_APP_CODE;
const [users, setUsers] = useState<YTUserSelectCode[]>([]);

<YtUserSelect
  appCode={appCode}
  notFoundContent="No users found"
  placeholder="Select users"
  value={users}
  onChange={setUsers}
/>`,
  addressPicker: `import { YtAddressPicker } from "@lovrabet/components";

const [address, setAddress] = useState(["zhejiang", "hangzhou"]);

<YtAddressPicker
  options={addressOptions}
  value={address}
  onChange={setAddress}
  placeholder="Select an address"
/>`,
  upload: `import { YtUpload } from "@lovrabet/components";

const [files, setFiles] = useState([]);

<YtUpload
  appCode={appCode}
  accept=".pdf,.docx,image/*"
  maxCount={3}
  maxSizeMB={20}
  fileList={files}
  onChange={({ fileList }) => setFiles(fileList)}
/>`,
  richEditor: `import { YTRichEditor } from "@lovrabet/components";

const [content, setContent] = useState("<p>Hello</p>");
const mockUpload = async (file) => URL.createObjectURL(file);

<YTRichEditor
  defaultValue={content}
  upload={mockUpload}
  onChange={setContent}
/>`,
  richEditorPreview: `import { YTRichEditor } from "@lovrabet/components";

<YTRichEditor.Preview content={content} />`,
  codeEditor: `import { YtCodeEditor } from "@lovrabet/components";

const [code, setCode] = useState("const ready = true;");

<YtCodeEditor
  language="javascript"
  value={code}
  onChange={setCode}
/>`,
  aiButton: `import { YTAIButton } from "@lovrabet/components";

<YTAIButton
  promptId={promptId}
  userPrompt="Summarize this customer note"
  onSuccess={setResult}
>
  Generate summary
</YTAIButton>`,
  aiInput: `import { YTAIInput } from "@lovrabet/components";

<YTAIInput
  promptId={promptId}
  inputType="textarea"
  userPromptTemplate="Improve this text: {input}"
  onSuccess={setResult}
/>`,
};

// 展示 AI 请求错误
const showAiError = (error: Error) => message.error(error.message);

type DemoSectionProps = {
  title: string;
  description: string;
  children: ReactNode;
  divided?: boolean;
};

// 渲染统一的组件示例章节
function DemoSection({
  title,
  description,
  children,
  divided,
}: DemoSectionProps) {
  return (
    <>
      {divided && <Divider />}
      <section className={styles.section}>
        <div className={styles.sectionHeading}>
          <Title level={2}>{title}</Title>
          <Text type="secondary">{description}</Text>
        </div>
        {children}
      </section>
    </>
  );
}

type DemoCardProps = {
  children: ReactNode;
  className: string;
  code: string;
  description: string;
  extra?: ReactNode;
  title: string;
};

type RichMode = "edit" | "preview";

// 渲染带代码展开开关的组件示例卡片
function DemoCard({
  children,
  className,
  code,
  description,
  extra,
  title,
}: DemoCardProps) {
  const [codeVisible, setCodeVisible] = useState(false);

  return (
    <Card className={className} extra={extra} title={title}>
      <Paragraph type="secondary">{description}</Paragraph>
      {children}
      <div
        className={`${styles.demoActions} ${codeVisible ? styles.demoActionsOpen : ""}`}
      >
        <Button
          aria-expanded={codeVisible}
          className={codeVisible ? styles.codeToggleActive : undefined}
          icon={<CodeOutlined />}
          type="text"
          onClick={() => setCodeVisible((visible) => !visible)}
        >
          {codeVisible ? "Hide code" : "Show code"}
        </Button>
      </div>
      {codeVisible && (
        <div className={styles.codePanel}>
          <YtCodeEditor
            height={240}
            language="javascript"
            readonly
            value={code}
            wordWrap="on"
          />
        </div>
      )}
    </Card>
  );
}

// 展示 Lovrabet 组件的交互示例
function ComponentsDemoPage() {
  const { token } = theme.useToken();
  const [users, setUsers] = useState<YTUserSelectCode[]>([]);
  const [address, setAddress] = useState<string[]>(["zhejiang", "hangzhou"]);
  const [files, setFiles] = useState<YtUploadFile[]>([]);
  const [richContent, setRichContent] = useState<string | null>(
    "<p>Use the shared editor for formatted business content.</p>",
  );
  const [code, setCode] = useState(() =>
    JSON.stringify(
      { appCode: LOVRABET_APP_CODE, page: "components-demo" },
      null,
      2,
    ),
  );
  const [buttonPromptId, setButtonPromptId] = useState("");
  const [buttonResult, setButtonResult] = useState("");
  const [inputPromptId, setInputPromptId] = useState("");
  const [inputResult, setInputResult] = useState("");
  const [richMode, setRichMode] = useState<RichMode>("edit");

  const activeButtonPromptId = buttonPromptId.trim() || undefined;
  const activeInputPromptId = inputPromptId.trim() || undefined;
  const pageStyle = {
    "--components-bg": token.colorBgContainer,
    "--components-bg-subtle": token.colorFillAlter,
    "--components-border": token.colorBorderSecondary,
    "--components-primary": token.colorPrimary,
    "--components-primary-bg": token.colorPrimaryBg,
    "--components-text": token.colorText,
    "--components-text-secondary": token.colorTextSecondary,
    "--components-radius": `${token.borderRadiusLG}px`,
  } as CSSProperties;

  // 校验 AI 输入是否可提交
  const validateAiInput = (value: string) => {
    if (!activeInputPromptId || !value.trim()) {
      message.warning(
        activeInputPromptId
          ? "Enter some text first"
          : "Enter a valid Prompt ID before calling the AI service",
      );
      return false;
    }
    return true;
  };

  return (
    <DemoPageContainer maxWidth={1280}>
      <main className={styles.page} style={pageStyle}>
        <header className={styles.hero}>
          <div>
            <Space size={8} wrap>
              <Tag color="blue">@lovrabet/components</Tag>
              <Tag>React 18</Tag>
              <Tag>Ant Design 5</Tag>
            </Space>
            <Title level={1}>Shared components, ready to reuse</Title>
            <Paragraph>
              Explore the Lovrabet component library in a real Rabetbase
              sub-app. Each section pairs a working example with code you can
              copy into a page.
            </Paragraph>
          </div>
          <Button
            href="https://www.npmjs.com/package/@lovrabet/components"
            target="_blank"
            rel="noopener noreferrer"
          >
            Open package docs
          </Button>
        </header>

        {!APP_CODE && (
          <Alert
            showIcon
            type="info"
            message="Configure a valid AppCode before using this page."
            description="Components that depend on app information cannot initialize without a valid AppCode. Pass --appcode when creating the project, or set the project AppCode and run rabetbase api pull before retrying."
          />
        )}

        <DemoSection
          title="Setup"
          description="Inject the request clients once at the app root; every component below inherits them."
        >
          <DemoCard
            title="Request clients"
            className={styles.demoCard}
            code={snippets.provider}
            description="src/main.tsx wraps the app with YtConfigProvider, so components never need their own apiFetch or runtimeFetch prop. Pass one on a single component only when it must reach a different deployment."
          >
            <Text className={styles.valuePreview} code>
              {JSON.stringify({
                apiFetch: apiFetch.baseURL,
                runtimeFetch: runtimeFetch.baseURL,
              })}
            </Text>
          </DemoCard>
        </DemoSection>

        <DemoSection
          title="Forms and business inputs"
          description="Use controlled values so these components work naturally with Ant Design forms."
        >
          <div className={styles.grid}>
            <DemoCard
              title="User selector"
              className={styles.demoCard}
              code={snippets.userSelect}
              description="Loads users for the current app and returns their user codes."
            >
              <YtUserSelect
                appCode={APP_CODE}
                disabled={!APP_CODE}
                notFoundContent="No users found"
                placeholder="Select users"
                value={users}
                onChange={setUsers}
              />
              <Text className={styles.valuePreview} code>
                {JSON.stringify(users)}
              </Text>
            </DemoCard>

            <DemoCard
              title="Address picker"
              className={styles.demoCard}
              code={snippets.addressPicker}
              description="Uses a controlled path and can also render a read-only preview."
            >
              <YtAddressPicker
                options={addressOptions}
                value={address}
                onChange={setAddress}
                placeholder="Select an address"
              />
              <div className={styles.previewLine}>
                <Text type="secondary">Preview</Text>
                <YtAddressPicker
                  options={addressOptions}
                  value={address}
                  preview
                />
              </div>
            </DemoCard>

            <DemoCard
              title="File upload"
              className={WIDE_CARD_CLASS_NAME}
              code={snippets.upload}
              description="Uploads through the Lovrabet file service for the configured app and demonstrates limits, previews, and controlled form values."
            >
              <YtUpload
                appCode={APP_CODE}
                accept=".pdf,.docx,image/*"
                maxCount={3}
                maxSizeMB={20}
                fileList={files}
                onChange={({ fileList }) => setFiles(fileList)}
              >
                <div className={styles.uploadPrompt}>
                  Select or drag up to three business files here
                </div>
              </YtUpload>
            </DemoCard>
          </div>
        </DemoSection>

        <DemoSection
          divided
          title="Content editors"
          description="Use the rich editor for business content and Monaco for structured text or code."
        >
          <div className={styles.grid}>
            <DemoCard
              title="Rich text"
              className={WIDE_CARD_CLASS_NAME}
              code={
                richMode === "edit"
                  ? snippets.richEditor
                  : snippets.richEditorPreview
              }
              description="Edit formatted business content or switch to a read-only preview."
              extra={
                <Segmented<RichMode>
                  options={[
                    { label: "Edit", value: "edit" },
                    { label: "Preview", value: "preview" },
                  ]}
                  value={richMode}
                  onChange={setRichMode}
                />
              }
            >
              {richMode === "edit" ? (
                <YTRichEditor
                  defaultValue={richContent}
                  loadingFallback={
                    <div className={styles.richEditorLoading}>
                      Loading rich editor…
                    </div>
                  }
                  upload={mockUpload}
                  onChange={setRichContent}
                />
              ) : (
                <YTRichEditor.Preview
                  className={styles.richPreview}
                  content={richContent}
                  loadingFallback={
                    <div
                      className={`${styles.richPreview} ${styles.richEditorLoading}`}
                    >
                      Loading preview…
                    </div>
                  }
                />
              )}
            </DemoCard>
            <DemoCard
              title="JSON editor"
              className={WIDE_CARD_CLASS_NAME}
              code={snippets.codeEditor}
              description="Edit structured JSON with Monaco-powered syntax highlighting."
            >
              <YtCodeEditor
                height={280}
                language="json"
                value={code}
                wordWrap="on"
                onChange={setCode}
              />
            </DemoCard>
          </div>
        </DemoSection>

        <DemoSection
          divided
          title="AI interactions"
          description="Connect the controls to a Prompt ID already configured for your app."
        >
          <Alert
            showIcon
            type="info"
            message="The demo does not include a shared Prompt ID. Enter one you are authorized to use before making a request."
          />
          <div className={`${styles.grid} ${styles.aiGrid}`}>
            <DemoCard
              title="AI button"
              className={styles.demoCard}
              code={snippets.aiButton}
              description="Trigger a predefined prompt from a standalone action button."
            >
              <label
                className={styles.fieldLabel}
                htmlFor="ai-button-prompt-id"
              >
                Prompt ID
              </label>
              <Input
                id="ai-button-prompt-id"
                placeholder="Enter a Prompt ID"
                value={buttonPromptId}
                onChange={(event) => setButtonPromptId(event.target.value)}
              />
              <div className={styles.aiControl}>
                <YTAIButton
                  disabled={!activeButtonPromptId}
                  gradient={false}
                  promptId={activeButtonPromptId}
                  userPrompt="Summarize the latest customer note"
                  onSuccess={setButtonResult}
                  onError={showAiError}
                >
                  Generate summary
                </YTAIButton>
              </div>
              {buttonResult && (
                <div className={styles.aiResult}>
                  <Text strong>Result</Text>
                  <Paragraph>{buttonResult}</Paragraph>
                </div>
              )}
            </DemoCard>

            <DemoCard
              title="AI input"
              className={styles.demoCard}
              code={snippets.aiInput}
              description="Collect user text and submit it through a reusable prompt template."
            >
              <label className={styles.fieldLabel} htmlFor="ai-input-prompt-id">
                Prompt ID
              </label>
              <Input
                id="ai-input-prompt-id"
                placeholder="Enter a Prompt ID"
                value={inputPromptId}
                onChange={(event) => setInputPromptId(event.target.value)}
              />
              <div className={styles.aiControl}>
                <YTAIInput
                  disabled={!activeInputPromptId}
                  gradient={false}
                  inputType="textarea"
                  promptId={activeInputPromptId}
                  placeholder="Enter text to improve"
                  buttonText="Improve text"
                  userPromptTemplate="Improve this business text while preserving its meaning: {input}"
                  textAreaProps={{ rows: 4 }}
                  onBeforeSubmit={validateAiInput}
                  onSuccess={setInputResult}
                  onError={showAiError}
                />
              </div>
              {inputResult && (
                <div className={styles.aiResult}>
                  <Text strong>Result</Text>
                  <Paragraph>{inputResult}</Paragraph>
                </div>
              )}
            </DemoCard>
          </div>
        </DemoSection>
      </main>
    </DemoPageContainer>
  );
}

ComponentsDemoPage.displayName = "ComponentsDemoPage";

export default ComponentsDemoPage;
