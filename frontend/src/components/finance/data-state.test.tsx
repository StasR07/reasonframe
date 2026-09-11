import { render, screen } from "@testing-library/react"
import { expect, it } from "vitest"
import { ResultState } from "./data-state"

it("explains unsupported results without rendering a chart", () => {
  render(<ResultState response={{ status: "UNSUPPORTED", series: [], errors: ["Gross margin is not meaningful for BRK.B"] }}/>)
  expect(screen.getByText("This request is not supported")).toBeInTheDocument(); expect(screen.getByText(/Gross margin is not meaningful/)).toBeInTheDocument()
})
