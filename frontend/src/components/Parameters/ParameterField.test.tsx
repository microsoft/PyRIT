import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";

import type { Parameter } from "@/types";

import ParameterField, { type ParameterFieldProps } from "./ParameterField";
import type { ParameterFormValue } from "./parameterForm";

// #3710: an external validationMessage marks the control invalid and describes it, without a second role="alert".

const MESSAGE = "Rejected by the server for this field";

function renderField(
  parameter: Parameter,
  value: ParameterFormValue,
  props: Partial<ParameterFieldProps> = {},
) {
  const tree = (extra: Partial<ParameterFieldProps>) => (
    <FluentProvider theme={webLightTheme}>
      <ParameterField
        parameter={parameter}
        value={value}
        disabled={false}
        onChange={jest.fn()}
        {...props}
        {...extra}
      />
    </FluentProvider>
  );
  const result = render(tree({}));
  return {
    ...result,
    rerenderWith: (extra: Partial<ParameterFieldProps>) => result.rerender(tree(extra)),
  };
}

/** Concatenated text of every element the control's aria-describedby references. */
function describedByText(element: Element): string {
  const ids = (element.getAttribute("aria-describedby") ?? "").split(/\s+/).filter(Boolean);
  return ids.map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
}

function expectNoAlertRole(container: HTMLElement): void {
  expect(container.querySelectorAll('[role="alert"]')).toHaveLength(0);
}

function expectMarkedInvalid(control: Element, container: HTMLElement): void {
  expect(control).toHaveAttribute("aria-invalid", "true");
  expect(describedByText(control)).toContain(MESSAGE);
  expect(screen.getByText(MESSAGE)).toBeVisible();
  expectNoAlertRole(container);
}

function expectNotMarkedInvalid(control: Element): void {
  expect(control).not.toHaveAttribute("aria-invalid", "true");
  expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
}

const SIMPLE_KINDS: Array<{
  kind: string;
  parameter: Parameter;
  value: ParameterFormValue;
  tagName: string;
}> = [
  {
    kind: "text",
    parameter: { name: "model_id", type_name: "str", required: true },
    value: "abc",
    tagName: "INPUT",
  },
  {
    kind: "number",
    parameter: { name: "max_tokens", type_name: "int", required: false },
    value: "5",
    tagName: "INPUT",
  },
  {
    kind: "list",
    parameter: { name: "stop", type_name: "list[str]", is_list: true, required: false },
    value: "a, b",
    tagName: "INPUT",
  },
  {
    kind: "multiline",
    parameter: { name: "http_request", type_name: "str", multiline: true, required: true },
    value: "not an HTTP request",
    tagName: "TEXTAREA",
  },
  {
    kind: "select",
    parameter: {
      name: "reasoning_effort",
      type_name: "str",
      choices: ["low", "high"],
      required: false,
    },
    value: "low",
    tagName: "SELECT",
  },
  {
    kind: "boolean",
    parameter: { name: "use_tls", type_name: "bool", required: false, default: "True" },
    value: "",
    tagName: "SELECT",
  },
];

describe("ParameterField validationMessage (#3710)", () => {
  it.each(SIMPLE_KINDS)(
    "marks the $kind control invalid and describes it with the message, then clears it",
    ({ parameter, value, tagName }) => {
      const { container, rerenderWith } = renderField(parameter, value, {
        validationMessage: MESSAGE,
      });
      const control = screen.getByTestId(`param-${parameter.name}`);
      expect(control.tagName).toBe(tagName);

      expectMarkedInvalid(control, container);

      // Regression guard: without the prop the control is not invalid.
      rerenderWith({ validationMessage: undefined });
      expectNotMarkedInvalid(screen.getByTestId(`param-${parameter.name}`));
    },
  );

  it("shows the external message on a valid JSON field and lets a local parse error win", () => {
    const parameter: Parameter = {
      name: "extra_body",
      type_name: "dict[str, typing.Any]",
      required: false,
    };
    const { container, rerender } = renderField(parameter, '{"a": 1}', {
      validationMessage: MESSAGE,
    });
    const control = screen.getByTestId("param-extra_body");
    expectMarkedInvalid(control, container);

    // Malformed JSON: the field's own parse error is the one shown.
    rerender(
      <FluentProvider theme={webLightTheme}>
        <ParameterField
          parameter={parameter}
          value="{bad"
          disabled={false}
          onChange={jest.fn()}
          validationMessage={MESSAGE}
        />
      </FluentProvider>,
    );
    expect(screen.getByText("extra_body must contain valid JSON.")).toBeVisible();
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
    expect(screen.getByTestId("param-extra_body")).toHaveAttribute("aria-invalid", "true");
  });

  it("puts a structured parameter's message on the variant select only", () => {
    const parameter: Parameter = {
      name: "scorer",
      type_name: "Scorer",
      required: false,
      variants: {
        SubStringScorer: [{ name: "substring", type_name: "str", required: true }],
      },
    };
    const { container, rerenderWith } = renderField(
      parameter,
      { type: "SubStringScorer", values: { substring: "x" } },
      { validationMessage: MESSAGE },
    );

    const variantSelect = screen.getByTestId("param-scorer");
    expect(variantSelect.tagName).toBe("SELECT");
    expectMarkedInvalid(variantSelect, container);
    expect(screen.getAllByText(MESSAGE)).toHaveLength(1);

    const nested = screen.getByTestId("param-scorer-substring");
    expect(nested).not.toHaveAttribute("aria-invalid", "true");
    expect(describedByText(nested)).not.toContain(MESSAGE);

    rerenderWith({ validationMessage: undefined });
    expectNotMarkedInvalid(screen.getByTestId("param-scorer"));
  });

  it("describes a multiselect group with the message and marks every checkbox invalid", () => {
    const parameter: Parameter = {
      name: "harm_categories",
      type_name: "list[str]",
      is_list: true,
      choices: ["hate", "violence"],
      required: false,
    };
    const { container, rerenderWith } = renderField(parameter, ["hate"], {
      validationMessage: MESSAGE,
    });

    const group = container.querySelector('[role="group"]');
    expect(group).not.toBeNull();
    expect(describedByText(group!)).toContain(MESSAGE);
    expect(screen.getByText(MESSAGE)).toBeVisible();
    for (const choice of ["hate", "violence"]) {
      expect(screen.getByTestId(`param-harm_categories-${choice}`)).toHaveAttribute(
        "aria-invalid",
        "true",
      );
    }
    expectNoAlertRole(container);

    rerenderWith({ validationMessage: undefined });
    expect(screen.queryByText(MESSAGE)).not.toBeInTheDocument();
    for (const choice of ["hate", "violence"]) {
      expect(screen.getByTestId(`param-harm_categories-${choice}`)).not.toHaveAttribute(
        "aria-invalid",
        "true",
      );
    }
  });
});

describe('ParameterField help', () => {
  beforeEach(() => jest.clearAllMocks())

  it.each(['str', 'int', 'float', 'Path'])('should not show a raw %s type hint', (typeName: string) => {
    render(<FluentProvider theme={webLightTheme}>
      <ParameterField parameter={{ name: 'Setting', type_name: typeName, required: false }}
        value="" disabled={false} onChange={jest.fn()} />
    </FluentProvider>)

    expect(screen.queryByText(typeName)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Setting')).toBeEnabled()
  })

  it('should expose the declared description through hover help and the control description', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}>
      <ParameterField parameter={{
        name: 'Objective', type_name: 'str', required: true,
        description: 'The behavior you want to test.',
      }} value="" disabled={false} onChange={jest.fn()} />
    </FluentProvider>)

    expect(screen.getByRole('textbox', { name: 'Objective' })).toBeRequired()
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveAccessibleDescription('The behavior you want to test.')
    await user.hover(screen.getByText('Objective'))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('The behavior you want to test.')
  })

  it('should keep list syntax and default guidance visible', () => {
    render(<FluentProvider theme={webLightTheme}>
      <ParameterField parameter={{
        name: 'Examples', type_name: 'list[str]', is_list: true, required: false, default: ['one', 'two'],
      }} value="" disabled={false} showDefaultHint onChange={jest.fn()} />
    </FluentProvider>)

    expect(screen.getByText(/Defaults to one, two\. Comma-separated list of values\./)).toBeVisible()
  })
})
