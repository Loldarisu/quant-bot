import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import List
from src.backtest.backtester import BacktestResult


def plot_equity_curve(result: BacktestResult, title: str = "Equity Curve") -> go.Figure:
    eq = result.equity_curve
    peak = eq.cummax()
    drawdown = (eq - peak) / peak * 100

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        subplot_titles=["Equity", "Drawdown %"],
    )

    fig.add_trace(go.Scatter(
        x=eq.index, y=eq.values,
        mode="lines", name="Equity",
        line=dict(color="#00d4aa", width=2),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=drawdown.index, y=drawdown.values,
        mode="lines", name="Drawdown",
        fill="tozeroy",
        line=dict(color="#ff4d4d", width=1),
    ), row=2, col=1)

    fig.update_layout(
        title=title,
        template="plotly_dark",
        height=600,
        showlegend=True,
        annotations=[
            dict(
                x=0.01, y=0.99, xref="paper", yref="paper",
                text=(
                    f"Total Return: {result.total_return_pct:.1f}%  |  "
                    f"Sharpe: {result.sharpe_ratio:.2f}  |  "
                    f"Max DD: {result.max_drawdown_pct:.1f}%  |  "
                    f"Win Rate: {result.win_rate_pct:.1f}%"
                ),
                showarrow=False,
                font=dict(size=12, color="white"),
                bgcolor="rgba(0,0,0,0.5)",
            )
        ],
    )
    return fig


def print_summary(result: BacktestResult):
    print("\n" + "=" * 50)
    print("  BACKTEST SUMMARY")
    print("=" * 50)
    print(f"  Total Return   : {result.total_return_pct:>10.2f}%")
    print(f"  CAGR           : {result.cagr_pct:>10.2f}%")
    print(f"  Sharpe Ratio   : {result.sharpe_ratio:>10.3f}")
    print(f"  Sortino Ratio  : {result.sortino_ratio:>10.3f}")
    print(f"  Max Drawdown   : {result.max_drawdown_pct:>10.2f}%")
    print(f"  Win Rate       : {result.win_rate_pct:>10.1f}%")
    print(f"  Profit Factor  : {result.profit_factor:>10.3f}")
    print(f"  Avg Trade      : {result.avg_trade_pct:>10.4f}%")
    print(f"  Total Trades   : {result.total_trades:>10}")
    print("=" * 50 + "\n")
