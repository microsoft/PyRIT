import { downloadFile } from "./downloadFile";

const WORD_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/** Observe the download link the helper creates, replacing its click with `onClick`. */
function spyOnDownloadLink(onClick: (link: HTMLAnchorElement) => void): () => HTMLAnchorElement | undefined {
  let link: HTMLAnchorElement | undefined;
  const createElement = document.createElement.bind(document);
  jest.spyOn(document, "createElement").mockImplementation((tagName: string) => {
    const element = createElement(tagName);
    if (element instanceof HTMLAnchorElement) {
      link = element;
      jest.spyOn(element, "click").mockImplementation(() => onClick(element));
    }
    return element;
  });
  return () => link;
}

describe("downloadFile", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("should click a link to the Blob under the requested filename, then remove it and revoke the URL", () => {
    const blob = new Blob(["report"], { type: WORD_MIME_TYPE });
    const clicks: Array<{ href: string; download: string; attached: boolean; revoked: boolean }> = [];
    const getLink = spyOnDownloadLink((link) => {
      clicks.push({
        href: link.href,
        download: link.download,
        attached: link.isConnected,
        revoked: jest.mocked(URL.revokeObjectURL).mock.calls.length > 0,
      });
    });

    downloadFile(blob, "copyrit-findings-operation-20261021T120000Z.docx");

    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
    expect(clicks).toEqual([{
      href: "blob:mock-url",
      download: "copyrit-findings-operation-20261021T120000Z.docx",
      attached: true,
      revoked: false,
    }]);
    expect(getLink()?.isConnected).toBe(false);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("should remove the link and revoke the URL when starting the download throws", () => {
    const getLink = spyOnDownloadLink(() => {
      throw new Error("download blocked");
    });

    expect(() => downloadFile(new Blob(["# Findings"], { type: "text/markdown" }), "findings.md"))
      .toThrow("download blocked");

    expect(getLink()?.isConnected).toBe(false);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });
});
