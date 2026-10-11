import Link from "next/link";
import { Report, duplicateOf, jobMessage } from "~~/utils/proofpay/reports";
import { settings } from "~~/utils/proofpay/settings";

const resultColor = { pass: "text-pass", warn: "text-warn", fail: "text-fail", skipped: "text-muted" };
export const ReportCard = ({ report, reviewBy }: { report: Report; reviewBy: bigint }) => {
  const duplicate = duplicateOf(report);
  return (
    <section className="card space-y-4 p-5" aria-label={`Attempt ${report.attempt} report`}>
      <div>
        <h2 className="font-semibold">Attempt {report.attempt} of 3</h2>
        <p
          className={
            report.attemptResult === "approved"
              ? "text-pass"
              : report.attemptResult === "rejected"
                ? "text-fail"
                : "text-muted"
          }
        >
          {report.attemptResult === "approved"
            ? "This attempt was approved"
            : report.attemptResult === "rejected"
              ? "This attempt was rejected"
              : jobMessage(report.jobState, reviewBy)}
        </p>
      </div>
      {duplicate && (
        <div className="rounded-card border border-fail p-4 text-sm" role="note">
          <p className="font-semibold text-fail">
            {duplicate.kind === "exact" ? "Duplicate photo" : "Nearly the same photo"}
          </p>
          <p className="mt-1">
            {duplicate.kind === "exact"
              ? "This exact photo was already accepted as proof for "
              : "This photo is very similar to the accepted proof for "}
            <Link className="link" href={`/tasks/${duplicate.taskId}`}>
              task #{duplicate.taskId}
            </Link>
            . One photo can approve only one task, so the payment stays locked.
          </p>
        </div>
      )}
      {report.settlement.conflict && (
        <p className="text-sm text-warn">
          The saved result differs from the local check. The recorded decision controls this attempt.
        </p>
      )}
      {report.evaluation ? (
        <>
          <p className="text-sm">{report.evaluation.reason}</p>
          {report.evaluation.checks.some(c => c.id === "V-C7" && c.result !== "skipped") && (
            // V-20: the score is the model's confidence; without a model result it is 0 and means nothing.
            <p className="text-xs text-muted">
              Confidence: {report.evaluation.score}/100. This is confidence in the decision, not work quality.
            </p>
          )}
          {report.attemptResult === "none" && (
            <p className="text-sm text-warn">These checks are not a recorded approval. The payment remains locked.</p>
          )}
          <ul className="divide-y divide-line">
            {report.evaluation.checks.map(check => (
              <li key={check.id} className="py-3 text-sm">
                <div className="flex justify-between gap-3">
                  <span>{check.name}</span>
                  <span className={`font-semibold capitalize ${resultColor[check.result]}`}>{check.result}</span>
                </div>
                {check.detail && <p className="mt-1 break-words text-xs text-muted">{check.detail}</p>}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="text-sm text-muted">No local evaluation is available yet.</p>
      )}
      <details className="border-t border-line pt-3 text-xs text-muted">
        <summary className="cursor-pointer">Details for this attempt</summary>
        <dl className="mt-3 space-y-2 break-all">
          <div>
            <dt>Proof hash</dt>
            <dd>{report.proofHash}</dd>
          </div>
          <div>
            <dt>Settlement</dt>
            <dd>{report.settlement.state}</dd>
          </div>
          {report.evaluation && (
            <>
              <div>
                <dt>Model</dt>
                <dd>{report.evaluation.model}</dd>
              </div>
              <div>
                <dt>Configuration</dt>
                <dd>{report.evaluation.configVersion}</dd>
              </div>
            </>
          )}
          {report.settlement.error && (
            <div>
              <dt>Service detail</dt>
              <dd>{report.settlement.error}</dd>
            </div>
          )}
        </dl>
        {report.settlement.txHashes.map(hash => (
          <a
            className="link mt-2 block break-all"
            key={hash}
            href={`${settings.explorerUrl}/tx/${hash}`}
            target="_blank"
            rel="noreferrer"
          >
            View transaction: {hash}
          </a>
        ))}
      </details>
    </section>
  );
};
